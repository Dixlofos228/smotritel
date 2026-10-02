import json
import pytest
from conftest import direct, OWNER
from smotritel.delivery import deliver
from smotritel.telegram import TelegramError


class Gateway:
    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self.next_message_id = 77

    async def call(self, method, **payload):
        self.calls.append((method, payload))
        assert not any(k.startswith("_") for k in payload)
        if len(self.calls) == 1 and self.error:
            raise self.error
        if method == "editMessageText":
            return {"message_id": payload["message_id"]}
        res = {"message_id": self.next_message_id}
        self.next_message_id += 1
        return res


def inline(route, message_id, update_id):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": {"id": OWNER},
            "data": f"u:{OWNER}:{route}",
            "message": {
                "message_id": message_id,
                "text": "Смотритель",
                "chat": {"id": OWNER, "type": "private"},
            },
        },
    }


async def test_direct_and_legacy_inline_requests_return_fresh_lower_keyboard(system):
    db, s, _, c = system
    db.run("DELETE FROM account_meta WHERE user_id=? AND key='ui_message_id'", (s.owner,))
    await s.handle(direct())
    row = db.one("SELECT * FROM outbox WHERE json_extract(payload,'$._ui_menu')=1 ORDER BY id DESC")
    tg = Gateway()
    await deliver(db, tg, c, row)
    assert s.db.meta("ui_message_id") == "77"
    await s.handle(direct(update_id=2))
    row = db.one("SELECT * FROM outbox ORDER BY id DESC")
    assert row["method"] == "sendMessage" and "message_id" not in json.loads(row["payload"])
    await deliver(db, tg, c, row)
    assert s.db.meta("ui_message_id") == "78"
    await s.handle(inline("settings", 78, 3))
    row = db.one("SELECT * FROM outbox ORDER BY id DESC")
    assert row["method"] == "sendMessage"
    assert "inline_keyboard" not in json.loads(row["payload"])["reply_markup"]
    await deliver(db, tg, c, row)
    count = db.one("SELECT count(*) n FROM outbox WHERE method='editMessageText'")["n"]
    await s.handle(inline("settings", 78, 4))
    assert db.one("SELECT count(*) n FROM outbox WHERE method='editMessageText'")["n"] == count == 0


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("Bad Request: message is not modified", ["editMessageText"]),
        ("Bad Request: message to edit not found", ["editMessageText", "sendMessage"]),
    ],
)
async def test_edit_telegram_recovery(system, reason, expected):
    db, s, _, c = system
    s.db.set_meta("ui_message_id", 55)

    s.db.enqueue("legacy-edit", "editMessageText", {"chat_id": OWNER, "message_id": 55, "text": "Menu"})
    row = db.one("SELECT * FROM outbox WHERE method='editMessageText' ORDER BY id DESC")
    tg = Gateway(TelegramError(400, description=reason))
    await deliver(db, tg, c, row)
    assert [x[0] for x in tg.calls] == expected
    assert db.one("SELECT state FROM outbox WHERE id=?", (row["id"],))["state"] == "sent"


async def test_upload_outside_archive_or_assets_is_refused(system):
    db, s, _, c = system
    s.db.enqueue("unsafe", "sendDocument", {"chat_id": OWNER, "_file": "/etc/passwd", "_field": "document"})
    with pytest.raises(ValueError):
        await deliver(db, Gateway(), c, db.one("SELECT * FROM outbox"))
