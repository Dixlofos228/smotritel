import json
from pathlib import Path

import pytest
from conftest import CID, OWNER, PEER, business, direct
from smotritel.delivery import deliver
from smotritel.media import Media
from smotritel.navigation import MAIN, MORE
from smotritel.service import Service
from test_delivery import Gateway, inline


BUTTONS = [
    ("🗂 Архив", "◈ Архив", ""),
    ("🗑 Удалённые", "◈ Удалённые", ""),
    ("✏️ Изменённые", "◈ Изменённые", ""),
    ("🤖 AI", "Напишите вопрос", "ask"),
    ("🎬 TikTok", "Пришлите публичную", "feature"),
    ("🌐 Перевод", "Перевод", ""),
    ("🔎 Яндекс", "Введите запрос", "feature"),
    ("🌤 Погода", "Введите город", "feature"),
    ("📊 Статистика", "Статистика архива", ""),
    ("⚙️ Настройки", "Настройки</b>", ""),
    ("🔌 Подключение", "◉ Подключение", ""),
    ("➕ Ещё", "<b>Ещё</b>", ""),
    ("🎙 Summary", "Пришлите голосовое", "voice"),
    ("💾 Сохранённые", "◈ Сохранённые", ""),
    ("📁 Медиа", "📎 Медиа", ""),
    ("👤 Профиль", "Регистрация:", ""),
    ("🧰 Инструменты", "<b>Инструменты</b>", ""),
    ("❓ Помощь", "Все функции", ""),
    ("🏠 Главная", "История остаётся с вами", ""),
    ("❌ Отмена", "Действие отменено", ""),
]


async def notifications_after_old_menu(db, s):
    await s.handle(business(10, "Received original"))
    await s.handle(business(11, "Edited version", edited=True))
    await s.handle(
        {
            "update_id": 12,
            "deleted_business_messages": {
                "business_connection_id": CID,
                "chat": {"id": PEER},
                "message_ids": [7],
            },
        }
    )
    assert db.one("SELECT count(*) n FROM outbox WHERE json_extract(payload,'$._ui_menu')=0")["n"] == 2

    assert s.db.meta("ui_message_id") == "55"


@pytest.mark.parametrize("label,expected,mode", BUTTONS)
async def test_every_lower_button_sends_its_screen_below_notifications(system, label, expected, mode):
    db, s, _, c = system
    assert {b for rows in (MAIN, MORE) for row in rows for b in row} | {"❌ Отмена"} == {
        b[0] for b in BUTTONS
    }
    await notifications_after_old_menu(db, s)
    s.db.set_meta("input_mode", "translate")
    s.db.set_meta("keyboard_layout", "more")

    root = Service(db, s.tg, s.ai, Media(db, s.tg, c), c)
    await root.handle(direct(text=label, update_id=100))
    rows = db.all("SELECT * FROM outbox WHERE event_key LIKE ? ORDER BY id", (f"{OWNER}:100:%",))
    assert rows and all(r["method"] == "sendMessage" for r in rows)
    res = rows[-1]
    p = json.loads(res["payload"])
    assert expected in p["text"] and "Не удалось" not in p["text"]
    assert "message_id" not in p and p["chat_id"] == OWNER
    assert s.db.meta("input_mode") == mode
    tg = Gateway()
    for row in rows:
        await deliver(db, tg, c, row)
    received = db.one("SELECT * FROM outbox WHERE id=?", (res["id"],))
    assert received["state"] == "sent" and received["response_message_id"] > 55
    if not mode and label != "❌ Отмена":
        assert s.db.meta("ui_message_id") == str(received["response_message_id"])
        assert p["reply_markup"]["keyboard"]
        assert "inline_keyboard" not in p["reply_markup"]
    markup = [json.loads(r["payload"]).get("reply_markup", {}) for r in rows]
    if label == "➕ Ещё":
        assert any(len(k.get("keyboard", [])) > len(MAIN) for k in markup)
        assert "launch:q" in s.db.meta("lower_routes")
    elif mode:
        assert any(["❌ Отмена", "🏠 Главная"] in k.get("keyboard", []) for k in markup)
    else:
        assert all(k.get("keyboard") for k in markup)
        assert "inline_keyboard" not in p["reply_markup"]


@pytest.mark.parametrize("label", [" ✏ Изменённые ", "🗑️  Удалённые", "💾\u00a0Сохранённые", "🗂Архив"])
async def test_client_emoji_and_spacing_variants_open_the_section(system, label):
    db, s, *_ = system
    await s.handle(direct(text=label, update_id=100))
    row = db.one("SELECT * FROM outbox ORDER BY id DESC")
    assert row["method"] == "sendMessage"
    p = json.loads(row["payload"])
    assert "◈ " in p["text"] and "Здесь пока пусто" in p["text"]
    assert p["reply_markup"]["keyboard"]
    assert "inline_keyboard" not in p["reply_markup"]


async def test_compact_tools_label_from_cached_keyboard_opens_tools(system):
    db, s, *_ = system
    await s.handle(direct(text="🧰Инструменты", update_id=100))
    p = json.loads(db.one("SELECT payload FROM outbox ORDER BY id DESC")["payload"])
    assert "<b>Инструменты</b>" in p["text"] and "input:translate" in s.db.meta("lower_routes")


@pytest.mark.parametrize(
    "route,expected",
    [("h", "История изменений"), ("save", "Добавлено в сохранённые"), ("txt", "отправлены файлом")],
)
async def test_notification_actions_answer_in_a_new_card_without_editing_old_menu(system, route, expected):
    db, s, _, c = system
    await notifications_after_old_menu(db, s)
    rid = db.one("SELECT rowid rid FROM messages WHERE message_id=7")["rid"]
    data = f"{route}:{rid}" + (":0" if route == "h" else "")
    await s.handle(inline(data, message_id=66, update_id=100))
    rows = db.all("SELECT * FROM outbox WHERE event_key LIKE ? ORDER BY id", (f"{OWNER}:100:%",))
    assert not any(r["method"] == "editMessageText" for r in rows)
    card = rows[-1]
    assert card["method"] == "sendMessage" and expected in json.loads(card["payload"])["text"]
    await deliver(db, Gateway(), c, card)
    assert s.db.meta("ui_message_id") == "77"
    if route == "save":
        assert db.one("SELECT message_id FROM saved")["message_id"] == 7
    if route == "txt":
        doc = next(json.loads(r["payload"]) for r in rows if r["method"] == "sendDocument")
        assert doc["_filename"] == "История сообщения.txt"
        assert "Received original" in Path(doc["_file"]).read_text()
        assert "Edited version" in Path(doc["_file"]).read_text()


async def test_unmapped_private_text_gets_a_visible_hint(system):
    db, s, *_ = system
    await s.handle(direct(text="Что дальше?", update_id=100))
    row = db.one("SELECT * FROM outbox ORDER BY id DESC")
    assert row["method"] == "sendMessage"
    p = json.loads(row["payload"])
    assert "Выберите раздел" in p["text"] and p["reply_markup"]["keyboard"] == MAIN
