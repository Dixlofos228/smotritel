#!/usr/bin/env python3

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smotritel.config import Config
from smotritel.db import DB, now
from smotritel.service import Service
from smotritel.media import Media


class OfflineTelegram:
    async def call(self, *args, **kwargs):
        raise AssertionError("Synthetic smoke must not call Telegram")


async def run():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        c = Config(root, owner_id=1)
        db = DB(root)
        tg = OfflineTelegram()
        s = Service(db, tg, None, Media(db, tg, c), c)
        await s.handle(
            {
                "update_id": 1,
                "business_connection": {
                    "id": "smoke",
                    "user": {"id": 1},
                    "user_chat_id": 1,
                    "date": now(),
                    "is_enabled": True,
                    "rights": {"can_reply": True},
                },
            }
        )

        def message(uid, mid, text, edited=False):
            return {
                "update_id": uid,
                "edited_business_message" if edited else "business_message": {
                    "business_connection_id": "smoke",
                    "chat": {"id": 2, "type": "private"},
                    "from": {"id": 2, "first_name": "Test"},
                    "date": now(),
                    "message_id": mid,
                    "text": text,
                },
            }

        await s.handle(message(2, 10, "TEST DELETE 12345"))
        await s.handle(message(3, 11, "TEST EDIT ORIGINAL"))
        db.close()
        db = DB(root)
        s = Service(db, tg, None, Media(db, tg, c), c)
        await s.handle(message(4, 11, "TEST EDIT CHANGED", True))
        await s.handle(
            {
                "update_id": 5,
                "deleted_business_messages": {
                    "business_connection_id": "smoke",
                    "chat": {"id": 2},
                    "message_ids": [10],
                },
            }
        )
        assert db.history("smoke", 2, 10)[0]["text"] == "TEST DELETE 12345"
        assert [v["text"] for v in db.history("smoke", 2, 11)] == ["TEST EDIT ORIGINAL", "TEST EDIT CHANGED"]
        assert db.one("SELECT deleted_at FROM messages WHERE message_id=10")["deleted_at"]
        assert any(
            "TEST DELETE 12345" in json.loads(r["payload"]).get("text", "")
            for r in db.all("SELECT payload FROM outbox")
        )
        print(
            "PASS synthetic delete/edit/restart/outbox smoke. Real Telegram connection NOT tested by this script"
        )
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
