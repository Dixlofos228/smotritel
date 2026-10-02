import asyncio
import json
import sqlite3
import sys
from pathlib import Path
import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smotritel.config import Config
from smotritel.telegram import Telegram, TelegramError


async def main():
    c = Config.load()
    db = sqlite3.connect(f"file:{c.data_dir}/archive.sqlite3?mode=ro", uri=True)
    async with aiohttp.ClientSession() as session:
        tg = Telegram(session, c.token)
        try:
            me = await tg.call("getMe")
            commands = await tg.call("getMyCommands")
            name = await tg.call("getMyName")
            desc = await tg.call("getMyDescription")
            photos = await tg.call("getUserProfilePhotos", user_id=me["id"], limit=1)
            webhook = await tg.call("getWebhookInfo")
            res = {
                "getMe": "PASS",
                "username": me["username"],
                "business_capability": bool(me.get("can_connect_to_business")),
                "bot_name": name["name"],
                "description_configured": bool(desc["description"]),
                "avatar_configured": photos["total_count"] > 0,
                "commands": [x["command"] for x in commands],
                "webhook_configured": bool(webhook["url"]),
            }
        except TelegramError as error:
            res = {"getMe": "UNAVAILABLE", "api_status": error.code}
    res.update(
        {
            "registered_accounts": db.execute("SELECT count(*) FROM accounts").fetchone()[0],
            "business_connections": db.execute("SELECT count(*) FROM connections WHERE enabled=1").fetchone()[
                0
            ],
            "archive_messages": db.execute("SELECT count(*) FROM messages").fetchone()[0],
            "archive_versions": db.execute("SELECT count(*) FROM versions").fetchone()[0],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "outbox_pending": db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],
            "outbox_failed": db.execute("SELECT count(*) FROM outbox WHERE state='failed'").fetchone()[0],
            "heartbeat": json.loads((c.data_dir / "heartbeat.json").read_text()),
        }
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
