import json
import pytest
from smotritel.config import Config
from smotritel.db import DB
from smotritel.service import Service
from smotritel.media import Media
from smotritel.ai import AI, AIUnavailable
from conftest import OWNER, PEER, CID, business, direct, FakeTelegram, FakeAI, connection, out_text


async def test_start_registers_independent_accounts_without_pairing(tmp_path):
    c = Config(tmp_path)
    db = DB(tmp_path)
    tg = FakeTelegram()
    s = Service(db, tg, FakeAI(), Media(db, tg, c), c)
    await s.handle(direct(OWNER, "/start", 1))
    await s.handle(direct(3003, "/start", 2))
    assert db.one("SELECT count(*) n FROM accounts")["n"] == 2
    assert s.owner == 0
    assert db.meta("owner_id") is None
    assert all(r["tenant_id"] in (OWNER, 3003) for r in db.all("SELECT * FROM outbox"))
    db.close()


async def test_peer_cannot_execute_dot_commands(system):
    db, s, *_ = system
    await s.handle(business(50, ".afk stolen"))
    assert not db.settings(CID, PEER).get("afk")
    assert not db.all("SELECT * FROM outbox")


async def test_foreign_connection_never_archived(system):
    db, s, tg, _ = system
    db.put_connection(connection(owner=9999, cid="foreign"))
    u = business()
    u["business_message"]["business_connection_id"] = "foreign"
    await s.handle(u)
    assert not db.all("SELECT * FROM messages")


async def test_missing_connection_resolved_only_for_owner(system):
    db, s, tg, _ = system
    db.run("DELETE FROM connections")
    await s.handle(business())
    assert tg.calls[0][0] == "getBusinessConnection"
    assert len(db.history(CID, PEER, 7)) == 1


async def test_afk_cooldown_unafk_and_business_bot_loop(system):
    db, s, *_ = system
    await s.handle(business(30, ".afk away", 30, owner=True))
    await s.handle(business(31, "hello", 31))
    await s.handle(business(32, "hello twice", 32))
    texts = [json.loads(r["payload"]).get("text", "") for r in db.all("SELECT payload FROM outbox")]
    assert texts.count("away") == 1
    await s.handle(business(33, ".unafk", 33, owner=True))
    assert not db.settings(CID, PEER).get("afk")
    await s.handle(business(34, ".afk loop", 34, owner=True, via_business_bot={"id": 555, "is_bot": True}))
    assert not db.settings(CID, PEER).get("afk")


async def test_edited_command_not_reexecuted(system):
    db, s, *_ = system
    await s.handle(business(30, ".afk changed", 30, owner=True, edited=True))
    assert not db.settings(CID, PEER).get("afk")


async def test_no_reply_rights_fails_honestly(system):
    db, s, *_ = system
    c = connection()
    c["rights"] = {}
    db.put_connection(c)
    await s.handle(business(4, ".ai question", 4, owner=True))
    assert "Нет can_reply" in out_text(db)
    assert all(
        "business_connection_id" not in json.loads(r["payload"]) for r in db.all("SELECT payload FROM outbox")
    )


async def test_help_save_stat_ai_summary(system):
    db, s, *_ = system
    orig = business(10, "save me", 10)
    await s.handle(orig)
    for i, cmd in enumerate((".help", ".save", ".stat", ".ai question"), 11):
        await s.handle(business(i, cmd, i, owner=True, reply_to_message=orig["business_message"]))
    assert db.one("SELECT COUNT(*) AS n FROM saved")["n"] == 1
    assert "Личный архив" in out_text(db) and "Сообщений:" in out_text(db)
    assert "LOCAL TEST ANSWER: question" in out_text(db)
    voice = business(20, None, 20, voice={"file_id": "v", "file_unique_id": "v", "file_size": 20})
    await s.handle(voice)
    await s.handle(business(21, ".summary", 21, owner=True, reply_to_message=voice["business_message"]))
    assert "Fixture speech transcript" in out_text(db)


async def test_safe_spam_test_chat_and_mock_payment(system):
    db, s, *_ = system
    await s.handle(business(1, ".spam 4 bad", 1, owner=True))
    assert "bad" not in out_text(db)
    await s.handle(business(2, ".spam 3 SAFE", 2, owner=True))
    assert out_text(db).count("SAFE") == 3
    await s.handle(business(3, ".spam 3 BLOCKED", 3, owner=True))
    assert "BLOCKED" not in out_text(db)
    await s.handle(business(4, ".send USDT 0.03", 4, owner=True))
    assert "DEMO" in out_text(db) and "НЕ выполнен" in out_text(db)


async def test_watch_history_is_observed_not_invented(system):
    db, s, *_ = system
    await s.handle(business(1, "hi", 1))
    await s.handle(business(2, ".watch", 2, owner=True))
    u = business(3, "changed", 3)
    u["business_message"]["from"]["username"] = "new_name"
    await s.handle(u)
    rows = db.all("SELECT * FROM user_history WHERE user_id=?", (PEER,))
    assert len(rows) == 2
    assert "Изменение наблюдаемого профиля" in out_text(db)
    await s.handle(business(4, ".search @new_name", 4, owner=True))
    assert "Истории до наблюдения нет" in out_text(db)


async def test_callback_owner_check_and_menu(system):
    db, s, *_ = system
    await s.handle(direct())
    count = len(db.all("SELECT * FROM outbox"))
    q = {
        "update_id": 2,
        "callback_query": {
            "id": "q",
            "from": {"id": 9999},
            "data": f"u:{OWNER}:archive",
            "message": {"chat": {"id": OWNER}},
        },
    }
    await s.handle(q)
    assert len(db.all("SELECT * FROM outbox")) == count
    q["update_id"] = 3
    q["callback_query"]["from"]["id"] = OWNER
    await s.handle(q)
    assert len(db.all("SELECT * FROM outbox")) == count + 2


async def test_external_ai_and_stt_refuse_before_any_http(tmp_path):
    class ForbiddenSession:
        def post(self, *args, **kwargs):
            raise AssertionError("Unexpected external transmission")

    c = Config(tmp_path, ai_url="https://outside.example/v1", stt_url="https://outside.example")
    ai = AI(c, ForbiddenSession())
    with pytest.raises(AIUnavailable, match="Внешний AI выключен"):
        await ai.answer("private chat text")
    with pytest.raises(AIUnavailable, match="только локальному"):
        await ai.transcript(tmp_path / "voice.ogg")


async def test_weather_disabled_sends_nothing(system):
    db, s, *_ = system
    await s.handle(business(1, ".weather Montevideo", 1, owner=True))
    assert "Погода выключена" in out_text(db)


async def test_tiktok_opt_in_guard(system):
    db, s, *_ = system
    await s.handle(business(100, ".tiktok https://www.tiktok.com/@test/video/123", 100, owner=True))
    assert "TikTok выключен" in out_text(db)


def test_tiktok_rejects_nonpublic_sources():
    from smotritel.tiktok import validate_url, DownloadUnavailable

    for link in (
        "http://www.tiktok.com/a",
        "https://localhost/a",
        "file:///etc/passwd",
        "https://www.tiktok.com@evil.test/a",
        "https://www.tiktok.com:8080/a",
    ):
        with pytest.raises(DownloadUnavailable):
            validate_url(link)
    assert validate_url("https://www.tiktok.com/@test/video/123")


async def test_personal_saved_archive_filters_other_messages(system):
    db, s, *_ = system
    await s.handle(business(1, "SAVED TEXT", 1))
    await s.handle(business(2, "OTHER TEXT", 2))
    await s.handle(
        business(3, ".save", 3, owner=True, reply_to_message=business(1, "SAVED TEXT", 1)["business_message"])
    )
    await s.handle(direct(OWNER, "/saved", 4))
    body = json.loads(db.one("SELECT payload FROM outbox ORDER BY id DESC")["payload"])
    assert "SAVED TEXT" in body["text"] and "OTHER TEXT" not in body["text"]
    assert body["reply_markup"]["keyboard"]


async def test_invalid_quote_image_is_reported_without_poisoning_worker(system):
    db, s, *_ = system
    photo = business(1, None, 1, photo=[{"file_id": "invalid-image", "file_unique_id": "invalid-image"}])
    await s.handle(photo)
    await s.handle(business(2, ".q", 2, owner=True, reply_to_message=photo["business_message"]))
    assert "недоступный локальный файл" in out_text(db)
    await s.handle(business(3, ".stat", 3, owner=True))
    assert "Сообщений:" in out_text(db)


async def test_power_commands_do_not_read_foreign_connection(system):
    from conftest import connection

    db, s, *_ = system
    db.put_connection(connection(owner=9999, cid="foreign"))
    foreign = business(1, "PRIVATE FOREIGN", 1)
    foreign["business_message"]["business_connection_id"] = "foreign"
    db.archive(foreign["business_message"], 1)
    await s.handle(direct(OWNER, "/history foreign 2002 1", 2))
    assert "PRIVATE FOREIGN" not in out_text(db)
    assert "недоступно" in out_text(db)
