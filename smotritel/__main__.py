import asyncio
import fcntl
import json
import logging
import os
import signal
import time
import aiohttp
from aiohttp import web
from .config import Config, secret
from .db import DB, now
from .telegram import Telegram, TelegramError
from .media import Media
from .ai import AI
from .service import Service
from .api import create_app
from .logging_safe import SafeFormatter
from .delivery import deliver, business_allowed, record_failure
from .branding import apply as apply_branding
from .config import local_url
from .tenancy import migrate_storage, owner_of
from .jobs import run_one

log = logging.getLogger("smotritel")
ALLOWED = [
    "message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


async def run():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    logging.getLogger("aiohttp.access").disabled = True
    c = Config.load()
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeFormatter((c.token, c.pairing_code, c.ai_key)))
    c.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (c.data_dir / "process.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another bot instance holds the archive lock") from None
        db = DB(c.data_dir)
        migrate_storage(db, c.data_dir)
        db.run("UPDATE jobs SET state='pending' WHERE state='running'")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        heartbeat = c.data_dir / "heartbeat.json"
        state = {"status": "waiting_token", "last_poll": 0, "last_worker": now(), "last_error": None}

        async def beat():
            while not stop.is_set():
                temp = heartbeat.with_suffix(".tmp")
                temp.write_text(json.dumps({**state, "time": now()}))
                os.replace(temp, heartbeat)
                await pause(5)

        async def pause(seconds):
            try:
                await asyncio.wait_for(stop.wait(), seconds)
            except TimeoutError:
                pass

        beat_task = asyncio.create_task(beat())
        while not c.token and not stop.is_set():
            log.warning("BOT_TOKEN is missing; waiting for the local secret file")
            await pause(30)
            c.token = secret("BOT_TOKEN")
        if stop.is_set():
            await beat_task
            db.close()
            return
        async with aiohttp.ClientSession() as session:
            tg = Telegram(session, c.token)
            svc = Service(db, tg, AI(c, session), Media(db, tg, c), c)
            while not stop.is_set():
                try:
                    me = await tg.call("getMe")
                    webhook = await tg.call("getWebhookInfo")
                    if webhook.get("url"):
                        log.error("Existing webhook detected; remove it deliberately before long polling")
                        state["status"] = "webhook_conflict"
                        await pause(30)
                        continue
                    log.info(
                        "Telegram identity verified; business capability=%s",
                        bool(me.get("can_connect_to_business")),
                    )
                    try:
                        await apply_branding(tg, db)
                    except (TelegramError, OSError) as error:
                        log.warning("Branding update deferred: type=%s", type(error).__name__)
                    commands = [
                        ("start", "Главное меню"),
                        ("help", "Все функции"),
                        ("profile", "Профиль и настройки"),
                        ("premium", "DEMO тариф"),
                        ("plugins", "Плагины"),
                        ("lang", "Язык"),
                        ("api", "Локальный API"),
                        ("archive", "Личный архив"),
                        ("saved", "Сохранённые сообщения"),
                        ("connect", "Подключение Business"),
                        ("settings", "Настройки"),
                        ("history", "Версии сообщения"),
                        ("media", "Локальные файлы"),
                        ("ai", "Локальный AI"),
                        ("summary", "Расшифровка voice"),
                        ("delete_data", "Удаление архива"),
                    ]
                    await tg.call(
                        "setMyCommands", commands=[{"command": k, "description": v} for k, v in commands]
                    )
                    db.set_meta("bot_username", me.get("username", ""))
                    db.set_meta("bot_id", me["id"])
                    db.set_meta("started_at", now())
                    db.set_meta("runtime_status", "running")
                    state["status"] = "running"
                    break
                except TelegramError as e:
                    state["status"] = "telegram_unavailable"
                    state["last_error"] = e.code
                    log.warning("Telegram initialization failed: status=%s", e.code)
                    await pause(max(10, e.retry_after))
            if stop.is_set():
                await beat_task
                db.close()
                return
            runner = web.AppRunner(create_app(db), access_log=None)
            await runner.setup()

            bind = os.getenv("API_BIND", "127.0.0.1")
            await web.TCPSite(runner, bind, 8787).start()

            async def poll():
                while not stop.is_set():
                    try:
                        updates = await tg.call(
                            "getUpdates",
                            offset=int(db.meta("offset", "0")),
                            timeout=25,
                            allowed_updates=ALLOWED,
                        )
                        db.ingest(updates)
                        state["last_poll"] = now()
                        state["last_error"] = None
                    except TelegramError as e:
                        state["last_error"] = e.code
                        log.warning("Polling retry: status=%s", e.code)
                        await pause(max(5, e.retry_after))

            async def process():
                while not stop.is_set():
                    rows = db.all(
                        "SELECT * FROM inbox WHERE state='pending' ORDER BY received_at,update_id LIMIT 1"
                    )
                    state["last_worker"] = now()
                    if not rows:
                        await pause(0.25)
                        continue
                    row = rows[0]
                    try:
                        await svc.handle(json.loads(row["payload"]))
                        db.run(
                            "UPDATE inbox SET state='done',payload='{}' WHERE update_id=?",
                            (row["update_id"],),
                        )
                    except Exception as e:
                        db.run("UPDATE inbox SET failures=failures+1 WHERE update_id=?", (row["update_id"],))
                        log.exception(
                            "Update retained for retry: type=%s id=%s", type(e).__name__, row["update_id"]
                        )
                        await pause(10)

            async def media_worker():
                while not stop.is_set():
                    row = db.one(
                        "SELECT f.* FROM media f JOIN versions v ON v.id=f.version_id LEFT JOIN connections c ON c.id=v.connection_id WHERE f.state='pending' AND f.next_attempt<=? AND coalesce((SELECT value FROM account_meta WHERE user_id=CASE WHEN v.connection_id LIKE 'dm:%' THEN v.chat_id ELSE c.owner_id END AND key='setting:media_save'),'1')='1' ORDER BY coalesce((SELECT CAST(value AS INTEGER) FROM account_meta WHERE user_id=CASE WHEN v.connection_id LIKE 'dm:%' THEN v.chat_id ELSE c.owner_id END AND key='last_media_at'),0),f.id LIMIT 1",
                        (now(),),
                    )
                    if not row:
                        await pause(1)
                        continue
                    try:
                        version = db.one(
                            "SELECT connection_id,chat_id FROM versions WHERE id=?", (row["version_id"],)
                        )
                        uid = owner_of(db, version["connection_id"], version["chat_id"])
                        if uid:
                            from .tenancy import TenantDB

                            TenantDB(db, uid).set_meta("last_media_at", now())
                            await svc.media.for_account(uid).fetch(row)
                    except Exception as e:
                        log.exception("Media retained for retry: type=%s id=%s", type(e).__name__, row["id"])
                        db.run("UPDATE media SET next_attempt=? WHERE id=?", (now() + 60, row["id"]))

            async def outbox():
                while not stop.is_set():
                    row = db.one(
                        "SELECT o.* FROM outbox o WHERE state='pending' AND next_attempt<=? ORDER BY coalesce((SELECT CAST(value AS INTEGER) FROM account_meta WHERE user_id=o.tenant_id AND key='last_delivery_at'),0),id LIMIT 1",
                        (now(),),
                    )
                    if not row:
                        await pause(0.25)
                        continue
                    body = json.loads(row["payload"])
                    if row["tenant_id"] and db.one("SELECT id FROM accounts WHERE id=?", (row["tenant_id"],)):
                        from .tenancy import TenantDB

                        TenantDB(db, row["tenant_id"]).set_meta("last_delivery_at", now())
                    cid = body.get("business_connection_id")
                    if cid and not business_allowed(db, row["tenant_id"], cid, row["method"]):
                        db.run("UPDATE outbox SET state='failed',error_code=403 WHERE id=?", (row["id"],))
                        record_failure(db, row)
                        continue
                    try:
                        await deliver(db, tg, c, row)
                    except TelegramError as e:
                        terminal = e.code in (400, 401, 403, 404)
                        if terminal:
                            record_failure(db, row)
                        attempts = row["attempts"] + 1
                        db.run(
                            "UPDATE outbox SET state=?,attempts=?,next_attempt=?,error_code=? WHERE id=?",
                            (
                                "failed" if terminal else "pending",
                                attempts,
                                now() + max(e.retry_after, min(300, 2 ** min(attempts, 8))),
                                e.code,
                                row["id"],
                            ),
                        )
                        log.warning(
                            "Outgoing delivery %s: status=%s", "failed" if terminal else "retry", e.code
                        )
                    except (OSError, ValueError):
                        record_failure(db, row)
                        db.run("UPDATE outbox SET state='failed',error_code=410 WHERE id=?", (row["id"],))
                        log.warning("Local upload file unavailable: outbox=%s", row["id"])
                    await pause(1)

            async def job_worker():
                while not stop.is_set():
                    if not await run_one(svc):
                        await pause(0.5)

            async def local_health():
                while not stop.is_set():
                    health = {"ai": False, "stt": False, "checked_at": now()}
                    for key, url in (
                        ("ai", c.ai_url.rstrip("/") + "/api/tags"),
                        ("stt", c.stt_url.rstrip("/") + "/health"),
                    ):
                        if not local_url(url):
                            continue
                        try:
                            async with session.get(
                                url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)
                            ) as resp:
                                res = await resp.json()
                                if key == "ai":
                                    health[key] = resp.status == 200 and any(
                                        m.get("name") == c.ai_model for m in res.get("models", [])
                                    )
                                else:
                                    health[key] = resp.status == 200 and res.get("ready") is True
                        except (aiohttp.ClientError, TimeoutError, ValueError):
                            pass
                    db.set_meta("local_health", json.dumps(health))
                    await pause(30)

            async def maintenance():
                while not stop.is_set():
                    from .clocks import tick

                    tick(db)
                    from .watch_sampling import sample

                    await sample(svc)
                    db.run("DELETE FROM inbox WHERE state='done' AND received_at<?", (now() - 7 * 86400,))
                    for folder in (c.data_dir / "tenants").glob("*/generated"):
                        for file in folder.rglob("*"):
                            if file.is_file() and file.stat().st_mtime < time.time() - 7 * 86400:
                                file.unlink()
                    db.run("DELETE FROM processed_actions WHERE applied_at<?", (now() - 7 * 86400,))
                    db.run(
                        "DELETE FROM jobs WHERE state IN ('done','failed') AND created_at<?",
                        (now() - 7 * 86400,),
                    )
                    db.run("PRAGMA wal_checkpoint(PASSIVE)")
                    await pause(60)

            tasks = [
                asyncio.create_task(f())
                for f in (poll, process, media_worker, outbox, maintenance, local_health, job_worker)
            ]
            stopper = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is not stopper and task.exception():
                        log.error("Background worker stopped: type=%s", type(task.exception()).__name__)
            finally:
                stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await runner.cleanup()
                await beat_task
                db.close()


if __name__ == "__main__":
    asyncio.run(run())
