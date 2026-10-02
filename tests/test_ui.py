import json
from html.parser import HTMLParser
import pytest
from conftest import OWNER, PEER, CID, business, direct, out_text
from smotritel.db import DB
from smotritel.ui import button
from smotritel.logging_safe import SafeFormatter
import logging


def payload(db):
    return json.loads(db.one("SELECT payload FROM outbox ORDER BY id DESC")["payload"])


def callback(route, update_id=101, uid=OWNER, chat=OWNER):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": {"id": uid},
            "data": f"u:{OWNER}:" + route,
            "message": {"message_id": 55, "text": "Смотритель", "chat": {"id": chat, "type": "private"}},
        },
    }


class TelegramHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "code"}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def valid_card(p):
    assert len(p["text"]) <= 4096
    parser = TelegramHTML()
    parser.feed(p["text"])
    assert not parser.stack
    assert "inline_keyboard" not in p["reply_markup"]
    assert p["reply_markup"]["is_persistent"]
    for row in p["reply_markup"]["keyboard"]:
        assert all(isinstance(label, str) and len(label) <= 60 for label in row)


def choices(service, p):
    from smotritel.navigation import normalize_label

    routes = json.loads(service.db.meta("lower_routes", "{}"))
    return [
        {"text": label, "callback_data": routes.get(normalize_label(label), "")}
        for row in p["reply_markup"]["keyboard"]
        for label in row
    ]


@pytest.mark.parametrize(
    "route",
    [
        "menu",
        "connect",
        "profile",
        "settings",
        "ai",
        "stats",
        "help",
        "help:archive",
        "help:business",
        "help:ai",
        "help:media",
        "help:stats",
        "help:tools",
        "help:connect",
        "media",
        "privacy",
        "retention",
        "a:all:0",
        "a:deleted:0",
        "a:edited:0",
        "a:saved:0",
        "a:photo:0",
        "a:voice:0",
        "a:document:0",
    ],
)
async def test_all_screens_use_lower_keyboard_and_fresh_response(system, route):
    db, s, *_ = system
    await s.handle(callback(route))
    p = payload(db)
    valid_card(p)
    assert db.one("SELECT method FROM outbox ORDER BY id DESC")["method"] == "sendMessage"
    if route != "menu":
        assert any(b["callback_data"] == "menu" for b in choices(s, p))
    assert CID not in p["text"] and '"can_reply"' not in p["text"]


async def test_menu_health_is_factual(system):
    db, s, *_ = system
    await s.handle(direct())
    assert "⚪ Local AI" in payload(db)["text"]
    db.set_meta("runtime_status", "running")
    db.set_meta("local_health", json.dumps({"ai": True, "stt": True}))
    await s.handle(callback("menu", 102))
    assert "🟢 Local AI" in payload(db)["text"] and "онлайн" in payload(db)["text"]


async def test_pagination_filters_and_back(system):
    db, s, *_ = system
    for n in range(1, 15):
        await s.handle(business(n, "TEXT " + str(n), n))
    await s.handle(callback("a:all:0"))
    first = payload(db)
    assert len([b for b in choices(s, first) if b["callback_data"].startswith("m:")]) == 6
    assert "a:all:1" in s.db.meta("lower_routes")
    await s.handle(callback("a:all:1", 102))
    second = payload(db)
    assert first["text"] != second["text"] and "a:all:0" in s.db.meta("lower_routes")
    await s.handle(callback("a:all:999", 103))
    assert "Страница 3 из 3" in payload(db)["text"]
    db.run("UPDATE messages SET deleted_at=1 WHERE message_id=3")
    await s.handle(business(20, "CHANGED", 4, edited=True))
    await s.handle(callback("a:deleted:0", 104))
    assert "TEXT 3" in payload(db)["text"] and "TEXT 4" not in payload(db)["text"]
    await s.handle(callback("a:edited:0", 105))
    assert "TEXT 4" in payload(db)["text"] and "TEXT 3" not in payload(db)["text"]
    rid = db.one("SELECT rowid rid FROM messages WHERE message_id=4")["rid"]
    await s.handle(callback(f"m:{rid}", 106))
    assert "a:edited:0" in s.db.meta("lower_routes")
    await s.handle(callback(f"h:{rid}:0", 107))
    assert "CHANGED" in payload(db)["text"] and "TEXT 4" in payload(db)["text"]
    await s.handle(callback(f"save:{rid}", 108))
    await s.handle(callback("a:saved:0", 109))
    assert "TEXT 4" in payload(db)["text"] and "TEXT 3" not in payload(db)["text"]


async def test_photo_voice_document_filters(system):
    db, s, *_ = system
    for n, kind in enumerate(("photo", "voice", "document"), 1):
        data = {"file_id": kind, "file_unique_id": kind}
        await s.handle(business(n, None, n, **{kind: [data] if kind == "photo" else data}))
    for n, kind in enumerate(("photo", "voice", "document"), 101):
        await s.handle(callback(f"a:{kind}:0", n))
        buttons = [b for b in choices(s, payload(db)) if b["callback_data"].startswith("m:")]
        assert len(buttons) == 1


async def test_notifications_original_before_after_and_disabled(system):
    db, s, *_ = system
    await s.handle(business(1, "ORIGINAL", 1))
    await s.handle(business(2, "CHANGED", 1, edited=True))
    p = payload(db)
    assert "Было" in p["text"] and "Стало" in p["text"]
    valid_card(p)
    deletion = {
        "update_id": 3,
        "deleted_business_messages": {
            "business_connection_id": CID,
            "chat": {"id": PEER},
            "message_ids": [1],
        },
    }
    await s.handle(deletion)
    p = payload(db)
    assert "ORIGINAL" in p["text"] and "CHANGED" not in p["text"]
    assert "inline_keyboard" not in p["reply_markup"]
    assert "ORIGINAL" in p["text"]
    await s.handle(callback("toggle:delete_notifications", 104))
    count = db.one("SELECT count(*) n FROM outbox")["n"]
    deletion["update_id"] = 5
    deletion["deleted_business_messages"]["message_ids"] = [999]
    await s.handle(deletion)
    assert db.one("SELECT count(*) n FROM outbox")["n"] == count
    assert db.one("SELECT deleted_at FROM messages WHERE message_id=999")["deleted_at"]


async def test_settings_survive_reopen_and_duplicate_retry(system):
    db, s, *_ = system
    await s.handle(callback("toggle:media_save"))
    assert not s.ui.setting("media_save")
    await s.handle(callback("toggle:media_save"))
    assert not s.ui.setting("media_save")
    other = DB(db.directory)
    from smotritel.tenancy import TenantDB

    assert TenantDB(other, OWNER).meta("setting:media_save") == "0"
    other.close()
    await s.handle(callback("language:en", 102))
    assert s.db.meta("lang") == "en"
    await s.handle(callback("settings", 103))
    assert "Settings" in payload(db)["text"]


async def test_owner_permissions_foreign_connection_and_private_dm(system):
    db, s, *_ = system
    before = db.one("SELECT count(*) n FROM outbox")["n"]
    await s.handle(callback("toggle:debug", uid=PEER))
    await s.handle(callback("menu", chat=PEER))
    assert db.one("SELECT count(*) n FROM outbox")["n"] == before and not s.ui.setting("debug", False)
    db.run(
        "INSERT INTO messages(connection_id,chat_id,message_id,first_seen) VALUES(?,?,?,?)",
        ("foreign", 7, 7, 1),
    )
    rid = db.one("SELECT rowid rid FROM messages WHERE connection_id='foreign'")["rid"]
    await s.handle(callback(f"m:{rid}", 103))
    assert "недоступно" in payload(db)["text"]


async def test_long_escaped_contents_and_history_pagination(system):
    db, s, *_ = system
    dangerous = '<b>secret</b> & "' * 2000
    await s.handle(business(1, dangerous, 1))
    for n in range(2, 8):
        await s.handle(business(n, dangerous + str(n), 1, edited=True))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    for n, route in enumerate((f"m:{rid}", f"h:{rid}:0", f"h:{rid}:1", f"h:{rid}:2"), 101):
        await s.handle(callback(route, n))
        p = payload(db)
        valid_card(p)
        assert "&lt;b&gt;" in p["text"] and "<b>secret</b>" not in p["text"]


async def test_ai_input_and_cancel(system):
    db, s, *_ = system
    await s.handle(callback("ask"))
    assert s.db.meta("input_mode") == "ask"
    await s.handle(direct(text="question", update_id=102))
    assert "LOCAL TEST ANSWER: question" in out_text(db)
    assert not s.db.meta("input_mode")
    await s.handle(callback("ask", 103))
    await s.handle(callback("menu", 104))
    assert not s.db.meta("input_mode")


async def test_voice_button_download_and_summary(system):
    db, s, *_ = system
    await s.handle(direct(text="", voice={"file_id": "voice", "file_unique_id": "voice"}, update_id=1))
    rid = db.one("SELECT rowid rid FROM messages WHERE connection_id='dm:1001'")["rid"]
    await s.handle(callback(f"sum:{rid}", 102))
    assert "Fixture speech transcript" in out_text(db) and "Кратко" in out_text(db)
    await s.handle(callback(f"f:{rid}", 103))
    assert db.one("SELECT count(*) n FROM outbox WHERE method='sendDocument'")["n"] == 1


async def test_global_afk_defaults_respect_chat_override(system):
    db, s, *_ = system
    await s.handle(callback("toggle:afk"))
    await s.handle(business(1, "hello", 1))
    assert "Сейчас недоступен" in out_text(db)
    await s.handle(business(2, ".unafk", 2, owner=True))
    before = out_text(db).count("Сейчас недоступен")
    await s.handle(business(3, "hello again", 3))
    assert out_text(db).count("Сейчас недоступен") == before


async def test_stale_bad_callback_error_is_friendly(system):
    db, s, *_ = system
    await s.handle(callback("m:invalid"))
    assert "Не удалось" in payload(db)["text"] and "ValueError" not in payload(db)["text"]
    await s.handle(callback("unknown", 102))
    assert "устарел" in payload(db)["text"]
    with pytest.raises(ValueError):
        button("x", "😀" * 17)


def test_logs_redact_credentials_and_exception_messages():
    secret = "123456:" + "abcdefghijklmnopqrstuvwxyz0123456789"
    formatter = SafeFormatter((secret, "pair-code", "ai-key"))
    try:
        raise ValueError("private chat " + secret)
    except ValueError:
        import sys

        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "Failure " + secret + " pair-code ai-key", (), sys.exc_info()
        )
        rendered = formatter.format(record)
    assert secret not in rendered and "pair-code" not in rendered and "ai-key" not in rendered
    assert "ValueError" in rendered and "Traceback" in rendered and "private chat" not in rendered


async def test_legacy_claim_link_is_not_required_and_reveals_no_secret(system):
    db, s, _, c = system
    await s.handle(direct(text="/start"))
    assert s.owner == OWNER and c.pairing_code not in out_text(db)


async def test_summary_failure_preserves_recognized_speech(system):
    from smotritel.ai import AIUnavailable

    db, s, *_ = system
    await s.handle(direct(text="", voice={"file_id": "voice", "file_unique_id": "voice"}, update_id=1))
    rid = db.one("SELECT rowid rid FROM messages WHERE connection_id='dm:1001'")["rid"]

    async def unavailable(*args, **kwargs):
        raise AIUnavailable("AI unavailable")

    s.ai.answer = unavailable
    await s.handle(callback(f"sum:{rid}", 102))
    assert "Fixture speech transcript" in out_text(db)
    assert "Краткий итог сейчас недоступен" in out_text(db)


async def test_notifications_never_overwrite_main_menu(system):
    db, s, *_ = system
    s.db.set_meta("ui_message_id", 555)
    await s.handle(business(1, "original", 1))
    await s.handle(business(2, "changed", 1, edited=True))
    row = db.one("SELECT method,payload FROM outbox ORDER BY id DESC")
    assert row["method"] == "sendMessage"
    assert not json.loads(row["payload"])["_ui_menu"]
    assert "message_id" not in json.loads(row["payload"])


async def test_edit_first_is_visible_and_never_called_original(system):
    db, s, *_ = system
    await s.handle(business(1, "EDIT FIRST", 1, edited=True))
    await s.handle(callback("a:edited:0", 102))
    assert "EDIT FIRST" in payload(db)["text"]
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    await s.handle(callback(f"m:{rid}", 103))
    assert "Самая ранняя доступная версия" in payload(db)["text"]
    assert "Полученный оригинал" not in payload(db)["text"]
    assert "Первоначальная версия не получена" in payload(db)["text"]


async def test_full_text_export_and_late_original(system):
    from pathlib import Path

    db, s, *_ = system
    await s.handle(business(1, "EDIT FIRST", 1, edited=True))
    await s.handle(business(2, "LATE ORIGINAL " + ("long text ") * 600, 1))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    await s.handle(callback(f"m:{rid}", 103))
    assert "LATE ORIGINAL" in payload(db)["text"]
    await s.handle(callback(f"txt:{rid}", 104))
    row = db.one("SELECT payload FROM outbox WHERE method='sendDocument' ORDER BY id DESC")
    body = Path(json.loads(row["payload"])["_file"]).read_text()
    assert "EDIT FIRST" in body and "LATE ORIGINAL" in body and "Версия 1" in body and "Версия 2" in body
    assert body.count("long text ") == 600
