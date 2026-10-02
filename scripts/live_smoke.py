import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp
from smotritel.ai import AI
from smotritel.config import Config, local_url
from smotritel.db import DB
from smotritel.logging_safe import SafeFormatter
from smotritel.media import Media
from smotritel.service import Service
from smotritel.telegram import Telegram


def account_id(db, args):
    accts = db.all("SELECT id FROM accounts")
    if args.account_id:
        assert any(a["id"] == args.account_id for a in accts), "Account is not registered"
        return args.account_id
    assert len(accts) == 1, "Explicit --account-id required unless exactly one account exists"
    return accts[0]["id"]


async def wait_sent(db, keys):
    until = time.monotonic() + 45
    while time.monotonic() < until:
        rows = [db.one("SELECT * FROM outbox WHERE event_key=?", (key,)) for key in keys]
        if any(not r or r["state"] == "failed" for r in rows):
            raise RuntimeError("Outbound smoke failed")
        if all(r["state"] == "sent" for r in rows):
            return rows
        await asyncio.sleep(0.5)
    raise TimeoutError("Outbound smoke timed out")


async def run(args):
    c = Config.load()
    logging.basicConfig(level=logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeFormatter((c.token, c.ai_key)))
    db = DB(c.data_dir)
    async with aiohttp.ClientSession() as session:
        tg = Telegram(session, c.token)
        me = await tg.call("getMe")
        report = {
            "getMe": "PASS",
            "username": me["username"],
            "human_start": "NOT EXECUTED",
            "human_callback": "NOT EXECUTED",
        }
        if args.send_menu:
            uid = account_id(db, args)
            ai = AI(c, session)
            actor = Service(db, tg, ai, Media(db, tg, c), c, account_id=uid)
            actor.event = "live-smoke-" + uuid.uuid4().hex
            await actor.panel("start")
            panel_rows = db.all(
                "SELECT * FROM outbox WHERE tenant_id=? AND event_key LIKE ?", (uid, f"{uid}:{actor.event}:%")
            )
            keyboard = any(
                json.loads(r["payload"]).get("reply_markup", {}).get("is_persistent") for r in panel_rows
            )
            inline = any(
                json.loads(r["payload"]).get("reply_markup", {}).get("inline_keyboard") for r in panel_rows
            )
            assert keyboard, "reply_keyboard_missing"
            assert not inline, "inline_menu_must_be_absent"
            assert local_url(c.ai_url), "Smoke requires local AI"
            status = actor.send(uid, "🤖 Проверяю обновление Смотрителя…", reply_menu=False)
            await wait_sent(db, [status])
            answer = await ai.answer("What is 2 + 2? Answer with one digit only")
            assert "4" in answer
            actor.progress = status
            res = actor.send(
                uid, "✅ Смотритель обновлён. Нижнее меню доступно; локальный AI проверен: 2 + 2 = 4"
            )
            keys = [r["event_key"] for r in panel_rows] + [status, res]
            rows = await wait_sent(db, keys)
            assert rows[-1]["response_message_id"] != rows[-2]["response_message_id"]
            report.update(
                {
                    "start_panel_outbound": "PASS",
                    "reply_keyboard_api": "PASS",
                    "inline_markup_absent": "PASS",
                    "local_ai": "PASS",
                    "status_deleted_and_replaced": "PASS",
                    "fixture_updates_in_production": 0,
                }
            )
        if args.navigation:
            uid = account_id(db, args)
            actor = Service(db, tg, AI(c, session), Media(db, tg, c), c, account_id=uid)
            actor.event = "live-navigation-" + uuid.uuid4().hex
            old_mid = int(actor.db.meta("ui_message_id", "0"))
            mids = []
            for route, want in (
                ("profile", "Регистрация:"),
                ("tools", "<b>Инструменты</b>"),
                ("a:all:0", "◈ Архив"),
            ):
                await actor.ui.navigate(route)
                row = db.one(
                    "SELECT * FROM outbox WHERE event_key=?",
                    (actor.db.event_key(f"{actor.event}:{actor.part}"),),
                )
                assert row["method"] == "sendMessage", "screen_must_be_fresh"
                body = json.loads(row["payload"])
                assert want in body["text"] and "message_id" not in body
                ack = (await wait_sent(db, [row["event_key"]]))[0]
                mids.append(ack["response_message_id"])
            assert len(set(mids)) == 3 and all(mid > old_mid for mid in mids)
            await actor.ui.navigate("settings")
            key = actor.db.event_key(f"{actor.event}:{actor.part}")
            row = db.one("SELECT * FROM outbox WHERE event_key=?", (key,))
            assert row["method"] == "sendMessage"
            assert "inline_keyboard" not in json.loads(row["payload"])["reply_markup"]
            ack = (await wait_sent(db, [key]))[0]
            assert ack["response_message_id"] > mids[-1]
            report.update(
                {
                    "fresh_profile_tools_archive_cards": "PASS",
                    "distinct_new_message_ids": "PASS",
                    "settings_lower_keyboard_api": "PASS",
                    "fixture_updates_in_production": 0,
                }
            )
        if args.features:
            uid = account_id(db, args)
            actor = Service(db, tg, AI(c, session), Media(db, tg, c), c, account_id=uid)
            actor.event = "live-features-" + uuid.uuid4().hex
            actor.animation(uid, actor.media.heart())
            actor.file(uid, actor.media.demo_check("USDT 0.03"), photo=True)
            weather = []

            def result(value):
                weather.append(value)
                actor.send(uid, value)

            await actor.weather("Montevideo", res)
            assert weather and "°C" in weather[0], "weather_api_unavailable"
            await actor.ui.navigate("catalog")
            queued = db.all(
                "SELECT * FROM outbox WHERE tenant_id=? AND event_key LIKE ? ORDER BY id",
                (uid, f"{uid}:{actor.event}:%"),
            )
            acks = await wait_sent(db, [row["event_key"] for row in queued])
            assert all(r["response_chat_id"] == uid for r in acks)
            conns = db.all("SELECT id FROM connections WHERE owner_id=? AND enabled=1", (uid,))
            rights = []
            for conn in conns:
                live = await tg.call("getBusinessConnection", business_connection_id=conn["id"])
                assert live["user"]["id"] == uid
                rights.append(
                    {
                        key: bool(live.get("rights", {}).get(key))
                        for key in ("can_reply", "can_manage_stories", "can_change_name", "can_change_bio")
                    }
                )
            report.update(
                {
                    "catalogue_api": "PASS",
                    "animation_upload_api": "PASS",
                    "demo_image_upload_api": "PASS",
                    "weather_live_api": "PASS",
                    "business_capabilities": rights,
                    "fixture_updates_in_production": 0,
                    "stories_published_by_smoke": 0,
                    "profile_changed_by_smoke": False,
                }
            )
        if args.lower:
            from smotritel.web_search import answer as search_answer
            from smotritel.tiktok import download_all

            uid = account_id(db, args)
            actor = Service(db, tg, AI(c, session), Media(db, tg, c), c, account_id=uid)
            actor.event = "live-lower-" + uuid.uuid4().hex
            await actor.ui.show("more")
            row = db.one("SELECT * FROM outbox ORDER BY id DESC")
            more = json.loads(row["payload"])["reply_markup"]
            assert len(more["keyboard"]) > 12 and "inline_keyboard" not in more
            await wait_sent(db, [row["event_key"]])
            prompt = actor.prompt("🔎 Проверяю поиск после обновления…")
            await wait_sent(db, [prompt])
            actor.dismiss_prompt()
            actor.lower.main()
            deletion = db.one("SELECT * FROM outbox ORDER BY id DESC")
            await wait_sent(db, [deletion["event_key"]])
            res = await search_answer(session, actor.ai, "что такое квен")
            assert "Источники (Bing)" in res and "https://" in res
            result_key = actor.send(uid, res)
            await wait_sent(db, [result_key])
            public_fixtures = [
                ("video", "https://vt.tiktok.com/ZSbfveptX/"),
                ("photos", "https://www.tiktok.com/@user/photo/7498443312253226258"),
            ]
            downloads = {}
            for kind, url in public_fixtures:
                actor.media.generated_capacity(c.max_media)
                files = await download_all(url, actor.media.generated, c.max_media, actor.media.assert_live)
                before = actor.part
                for file in files:
                    actor.file(
                        uid, file, photo=kind == "photos", filename="TikTok без водяного знака" + file.suffix
                    )
                keys = [actor.db.event_key(f"{actor.event}:{i}") for i in range(before + 1, actor.part + 1)]
                acks = await wait_sent(db, keys)
                assert all(r["response_chat_id"] == uid for r in acks)
                downloads[kind] = {
                    "download": "PASS",
                    "telegram_upload": "PASS",
                    "files": len(files),
                    "bytes": sum(f.stat().st_size for f in files),
                }
            await actor.ui.show("menu")
            key = actor.db.event_key(f"{actor.event}:{actor.part}")
            await wait_sent(db, [key])
            report.update(
                {
                    "lower_menu_api": "PASS",
                    "more_long_list_api": "PASS",
                    "prompt_delete_api": "PASS",
                    "web_answer_live": "PASS",
                    "search_source": "Bing",
                    "tiktok": downloads,
                    "fixture_updates_in_production": 0,
                    "human_button_presses": "NOT EXECUTED",
                }
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-menu", action="store_true")
    parser.add_argument("--navigation", action="store_true")
    parser.add_argument("--features", action="store_true")
    parser.add_argument("--lower", action="store_true")
    parser.add_argument("--account-id", type=int)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as error:
        trace = error.__traceback__
        while trace.tb_next:
            trace = trace.tb_next
        print(
            json.dumps(
                {"live_smoke": "FAIL", "error_type": type(error).__name__, "source_line": trace.tb_lineno}
            )
        )
        raise SystemExit(1) from None
