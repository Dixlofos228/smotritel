#!/usr/bin/env python3

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp
from smotritel.config import Config, local_url
from smotritel.ai import AI
from smotritel.db import DB, now
from smotritel.media import Media
from smotritel.service import Service


async def run():
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav"
        ) as r:
            if r.status != 200:
                raise RuntimeError("Public audio fixture could not be downloaded")
            fixture = await r.read()
        with tempfile.TemporaryDirectory(dir=os.getenv("DATA_DIR", "/data")) as tmp:
            c = Config.load()
            assert local_url(c.ai_url) and local_url(c.stt_url), "This smoke requires local providers"
            c.data_dir = Path(tmp)
            c.owner_id = 1
            ai = AI(c, session)
            answer = await ai.answer("What is 2 + 2? Answer with one digit only")
            assert "4" in answer, "Local AI arithmetic smoke failed"
            path = Path(tmp) / "jfk.wav"
            path.write_bytes(fixture)
            txt = await ai.transcript(path)
            assert "country" in txt.lower(), "STT did not recognize the known public test recording"
            brief = await ai.answer(txt, "Summarize this speech in one short sentence")
            assert brief.strip()

            class FixtureTelegram:
                async def download(self, file_id, target, limit):
                    target.write_bytes(fixture)
                    return {"file_path": "voice/jfk.wav"}

            tg = FixtureTelegram()
            db = DB(Path(tmp))
            s = Service(db, tg, ai, Media(db, tg, c), c)
            db.put_connection(
                {
                    "id": "runtime-smoke",
                    "user": {"id": 1},
                    "user_chat_id": 1,
                    "is_enabled": True,
                    "date": now(),
                    "rights": {"can_reply": True},
                }
            )
            voice = {
                "business_connection_id": "runtime-smoke",
                "message_id": 10,
                "date": now(),
                "from": {"id": 2, "first_name": "Fixture"},
                "chat": {"id": 2, "type": "private"},
                "voice": {"file_id": "jfk", "file_unique_id": "jfk", "file_size": len(fixture)},
            }
            await s.handle({"update_id": 10, "business_message": voice})
            await s.handle(
                {
                    "update_id": 11,
                    "business_message": {
                        "business_connection_id": "runtime-smoke",
                        "message_id": 11,
                        "date": now(),
                        "from": {"id": 1},
                        "chat": {"id": 2, "type": "private"},
                        "text": ".summary",
                        "reply_to_message": voice,
                    },
                }
            )
            from smotritel.jobs import run_one

            await run_one(s)
            texts = [
                json.loads(row["payload"]).get("text", "") for row in db.all("SELECT payload FROM outbox")
            ]
            assert any("Расшифровка:" in t and "Кратко" in t and "country" in t.lower() for t in texts)
            print(
                json.dumps(
                    {
                        "model": c.ai_model,
                        "local_ai": "PASS",
                        "answer": answer,
                        "local_stt": "PASS",
                        "transcript": txt,
                        "summary": brief,
                        "summary_command_with_real_local_models": "PASS",
                        "telegram_transport": "SYNTHETIC; live E2E still required",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            db.close()


if __name__ == "__main__":
    asyncio.run(run())
