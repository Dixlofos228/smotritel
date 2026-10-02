#!/usr/bin/env python3

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smotritel.db import DB, now
from smotritel.config import Config
from smotritel.media import Media
from smotritel.service import Service


class FixtureTelegram:
    async def download(self, file_id, target, limit):
        target.write_bytes(b"docker volume media fixture")
        return {"file_path": "documents/fixture.bin"}


async def run(phase):
    root = Path(os.getenv("DATA_DIR", "/data"))
    c = Config(root, owner_id=1)
    db = DB(root)
    tg = FixtureTelegram()
    s = Service(db, tg, None, Media(db, tg, c), c)

    def message(update, mid, text, edited=False, **extra):
        return {
            "update_id": update,
            "edited_business_message" if edited else "business_message": {
                "business_connection_id": "volume-test",
                "chat": {"id": 2, "type": "private"},
                "from": {"id": 2},
                "date": now(),
                "message_id": mid,
                "text": text,
                **extra,
            },
        }

    if phase == "seed":
        assert not db.all("SELECT * FROM messages"), "Use a fresh dedicated test volume"
        db.put_connection(
            {
                "id": "volume-test",
                "user": {"id": 1},
                "user_chat_id": 1,
                "date": now(),
                "is_enabled": True,
                "rights": {"can_reply": True},
            }
        )
        await s.handle(
            message(1, 10, "TEST DELETE 12345", document={"file_id": "fixture", "file_unique_id": "fixture"})
        )
        await s.handle(message(2, 11, "TEST EDIT ORIGINAL"))
        await s.media.for_account(1).ensure([db.history("volume-test", 2, 10)[0]["id"]])
        print("PASS phase 1: original messages and actual fixture media saved in dedicated Docker volume")
    elif phase == "finish":
        assert db.history("volume-test", 2, 10)[0]["text"] == "TEST DELETE 12345"
        media = db.one("SELECT * FROM media")
        assert s.media.for_account(1).path(media).read_bytes() == b"docker volume media fixture"
        await s.handle(message(3, 11, "TEST EDIT CHANGED", True))
        await s.handle(
            {
                "update_id": 4,
                "deleted_business_messages": {
                    "business_connection_id": "volume-test",
                    "chat": {"id": 2},
                    "message_ids": [10],
                },
            }
        )
        assert [r["text"] for r in db.history("volume-test", 2, 11)] == [
            "TEST EDIT ORIGINAL",
            "TEST EDIT CHANGED",
        ]
        assert db.one("SELECT deleted_at FROM messages WHERE message_id=10")["deleted_at"]
        assert any(
            "TEST DELETE 12345" in json.loads(r["payload"]).get("text", "")
            for r in db.all("SELECT payload FROM outbox")
        )
        print(
            "PASS phase 2: a new container recovered persisted text/media, saved edit history and deletion/outbox. Telegram updates were synthetic"
        )
    else:
        raise ValueError("Expected seed or finish")
    db.close()


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
