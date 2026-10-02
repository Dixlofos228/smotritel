import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
import aiohttp
import pytest
from aiohttp import web
from smotritel.config import Config
from smotritel.db import DB, now, dump
from smotritel.service import Service
from smotritel.media import Media
from smotritel.tenancy import TenantDB, migrate_storage
from smotritel.delivery import deliver
from smotritel.jobs import run_one
from conftest import FakeAI, FakeTelegram, direct

A, B = 111, 222
CA, CB = "business-A", "business-B"


def connection(uid, cid):
    return {
        "id": cid,
        "user": {"id": uid, "first_name": "User " + str(uid)},
        "user_chat_id": uid,
        "date": now(),
        "is_enabled": True,
        "rights": {"can_reply": True},
    }


def event(uid, cid, update=1, text="PRIVATE", mid=7, edited=False, media=None, sender=None):
    m = {
        "message_id": mid,
        "date": now(),
        "business_connection_id": cid,
        "from": {"id": sender or 999, "first_name": "Sender"},
        "chat": {"id": 999, "type": "private"},
        "text": text,
    }
    if media:
        m.update(media)
    return {"update_id": update, "edited_business_message" if edited else "business_message": m}


def cb(uid, route, update=100, claimed_uid=None):
    return {
        "update_id": update,
        "callback_query": {
            "id": "q" + str(update),
            "from": {"id": uid},
            "data": f"u:{claimed_uid or uid}:" + route,
            "message": {"chat": {"id": uid, "type": "private"}, "message_id": 55, "text": "menu"},
        },
    }


def texts(db, uid):
    return "\n".join(
        json.loads(r["payload"]).get("text", "")
        for r in db.all("SELECT payload FROM outbox WHERE tenant_id=?", (uid,))
    )


@pytest.fixture
async def tenants(tmp_path):
    c = Config(tmp_path)
    db = DB(tmp_path)
    tg = FakeTelegram()
    s = Service(db, tg, FakeAI(), Media(db, tg, c), c)
    for n, (uid, cid) in enumerate(((A, CA), (B, CB)), 1):
        await s.handle(direct(uid, "/start", n))
        await s.handle({"update_id": n + 10, "business_connection": connection(uid, cid)})
    yield db, s, tg, c
    db.close()


async def test_auto_signup_has_no_global_owner_or_pairing(tenants):
    db, s, *_ = tenants
    assert db.one("SELECT count(*) n FROM accounts")["n"] == 2 and db.meta("owner_id") is None
    assert s.owner == 0
    assert "/claim" not in texts(db, A) + texts(db, B)
    for uid in (A, B):
        keyboards = [
            json.loads(r["payload"]).get("reply_markup", {})
            for r in db.all("SELECT payload FROM outbox WHERE tenant_id=?", (uid,))
        ]
        assert any(k.get("resize_keyboard") and k.get("is_persistent") for k in keyboards)


@pytest.mark.parametrize("route", ["a:all:0", "a:deleted:0", "a:edited:0", "stats", "profile", "a:saved:0"])
async def test_archive_stats_filters_profiles_are_tenant_scoped(tenants, route):
    db, s, *_ = tenants
    await s.handle(event(A, CA, 20, "A PRIVATE"))
    await s.handle(event(B, CB, 21, "B PRIVATE"))
    await s.handle(event(B, CB, 22, "B CHANGED", edited=True))
    db.run("UPDATE messages SET deleted_at=1")
    db.run("INSERT INTO saved VALUES(?,?,?,?)", (CB, 999, 7, now()))
    await s.handle(cb(A, route, 30))
    row = db.one("SELECT payload FROM outbox WHERE tenant_id=? ORDER BY id DESC", (A,))
    assert (
        "B PRIVATE" not in row["payload"]
        and "B CHANGED" not in row["payload"]
        and "User 222" not in row["payload"]
    )
    actor = Service(db, s.tg, s.ai, s.media, s.c, account_id=A)
    assert actor.ui.totals() == {"messages": 1, "deleted": 1, "edited": 0, "files": 0, "saved": 0}


@pytest.mark.parametrize("route", ["m", "h", "save", "f", "txt", "sum"])
async def test_guessed_foreign_row_callback_cannot_read_or_operate(tenants, route):
    db, s, tg, c = tenants
    await s.handle(event(B, CB, 20, "B PRIVATE", media={"voice": {"file_id": "vb", "file_unique_id": "vb"}}))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    before = len(tg.calls)
    await s.handle(cb(A, f"{route}:{rid}", 30))
    assert "B PRIVATE" not in texts(db, A)
    assert not db.one("SELECT * FROM saved") and not db.one("SELECT * FROM jobs")
    assert len(tg.calls) == before


async def test_forged_tenant_prefix_rejected_before_mutation(tenants):
    db, s, *_ = tenants
    await s.handle(cb(A, "toggle:media_save", 30, claimed_uid=B))
    assert TenantDB(db, B).meta("setting:media_save") is None
    assert TenantDB(db, A).meta("setting:media_save") is None
    assert (
        "устарело" in texts(db, A)
        or db.one("SELECT method FROM outbox WHERE event_key LIKE '111:30:%'")["method"]
        == "answerCallbackQuery"
    )


@pytest.mark.parametrize("cmd", ["history", "media"])
async def test_guessed_business_connection_power_commands_rejected(tenants, cmd):
    db, s, *_ = tenants
    await s.handle(event(B, CB, 20, "B PRIVATE"))
    await s.handle(direct(A, f"/{cmd} {CB} 999 7", 30))
    assert "B PRIVATE" not in texts(db, A) and "недоступно" in texts(db, A)


async def test_delete_notifications_route_exactly_to_account_owner(tenants):
    db, s, *_ = tenants
    await s.handle(event(A, CA, 20, "A ORIGINAL"))
    await s.handle(event(B, CB, 21, "B ORIGINAL"))
    for n, (uid, cid) in enumerate(((A, CA), (B, CB)), 30):
        await s.handle(
            {
                "update_id": n,
                "deleted_business_messages": {
                    "business_connection_id": cid,
                    "chat": {"id": 999},
                    "message_ids": [7],
                },
            }
        )
    assert "A ORIGINAL" in texts(db, A) and "B ORIGINAL" not in texts(db, A)
    assert "B ORIGINAL" in texts(db, B) and "A ORIGINAL" not in texts(db, B)
    rows = db.all(
        "SELECT tenant_id,payload FROM outbox WHERE json_extract(payload,'$.text') LIKE '%Удалённое сообщение%'"
    )
    assert len(rows) == 2 and all(json.loads(r["payload"])["chat_id"] == r["tenant_id"] for r in rows)


async def test_per_user_settings_and_state_are_independent(tenants):
    db, s, *_ = tenants
    await s.handle(cb(A, "toggle:edit_notifications", 30))
    await s.handle(cb(B, "toggle:media_save", 31))
    await s.handle(direct(A, "🤖 AI", 32))
    assert TenantDB(db, A).meta("input_mode") == "ask" and not TenantDB(db, B).meta("input_mode")
    assert (
        TenantDB(db, A).meta("setting:edit_notifications") == "0"
        and TenantDB(db, B).meta("setting:edit_notifications") is None
    )
    assert (
        TenantDB(db, B).meta("setting:media_save") == "0"
        and TenantDB(db, A).meta("setting:media_save") is None
    )
    await s.handle(direct(A, "🏠 Главная", 33))
    assert not TenantDB(db, A).meta("input_mode")


async def test_media_namespace_and_forged_paths(tenants):
    db, s, tg, c = tenants
    for n, (uid, cid) in enumerate(((A, CA), (B, CB)), 20):
        await s.handle(
            event(uid, cid, n, None, media={"document": {"file_id": "same-bytes", "file_unique_id": "same"}})
        )
    actors = {uid: Service(db, tg, s.ai, s.media, c, account_id=uid) for uid in (A, B)}
    for row in db.all("SELECT * FROM media"):
        uid = A if row["id"] == 1 else B
        await actors[uid].media.fetch(row)
    rows = db.all("SELECT * FROM media ORDER BY id")
    assert rows[0]["path"] != rows[1]["path"] and "/111/" in rows[0]["path"] and "/222/" in rows[1]["path"]
    with pytest.raises(ValueError):
        actors[A].media.path(rows[1])
    with pytest.raises(ValueError):
        await actors[A].media.fetch(rows[1])
    forged = dict(rows[0], path=rows[1]["path"])
    with pytest.raises(ValueError):
        actors[A].media.path(forged)
    actors[A].db.enqueue(
        "forged-upload",
        "sendDocument",
        {"chat_id": A, "_file": str(actors[B].media.path(rows[1])), "_field": "document"},
    )
    with pytest.raises(ValueError):
        await deliver(db, tg, c, db.one("SELECT * FROM outbox WHERE event_key='111:forged-upload'"))


async def test_api_key_resolves_only_own_tenant(tenants):
    from smotritel.api import create_app

    db, s, *_ = tenants
    await s.handle(event(A, CA, 20, "A PRIVATE"))
    await s.handle(event(B, CB, 21, "B PRIVATE"))
    TenantDB(db, A).set_meta("api_hash", hashlib.sha256(b"key-A").hexdigest())
    TenantDB(db, B).set_meta("api_hash", hashlib.sha256(b"key-B").hexdigest())
    runner = web.AppRunner(create_app(db), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as client:
            headers = {"Authorization": "Bearer key-A"}
            url = f"http://127.0.0.1:{port}"
            async with client.get(url + "/archive?limit=1&tenant_id=222", headers=headers) as r:
                data = await r.text()
                assert r.status == 200 and CA in data and CB not in data
            async with client.get(url + f"/message/{CB}/999/7", headers=headers) as r:
                assert r.status == 404 and "B PRIVATE" not in await r.text()
            async with client.get(url + "/health", headers=headers) as r:
                assert (await r.json())["connections"] == 1
    finally:
        await runner.cleanup()


async def test_delete_my_data_removes_only_confirming_user_and_stops_rearchive(tenants):
    db, s, tg, c = tenants
    for n, (uid, cid) in enumerate(((A, CA), (B, CB)), 20):
        await s.handle(
            event(
                uid,
                cid,
                n,
                "Private " + str(uid),
                media={"document": {"file_id": str(uid), "file_unique_id": str(uid)}},
            )
        )
        actor = Service(db, tg, s.ai, s.media, c, account_id=uid)
        await actor.media.fetch(db.one("SELECT * FROM media WHERE id=?", (n - 19,)))
        actor.db.set_meta("input_mode", "ask")
        actor.db.set_meta("api_hash", "hash" + str(uid))
        db.run("INSERT INTO watches VALUES(?,?)", (cid, 999))
        db.run("INSERT INTO saved VALUES(?,?,?,?)", (cid, 999, 7, now()))
    bpath = Path(c.data_dir / db.one("SELECT path FROM media WHERE id=2")["path"])
    await s.handle(direct(A, "/delete_data", 30))
    assert db.one("SELECT id FROM accounts WHERE id=?", (A,))
    nonce = TenantDB(db, A).meta("delete_nonce")
    await s.handle(cb(A, "erase:" + nonce, 31))
    assert not db.one("SELECT id FROM accounts WHERE id=?", (A,)) and not db.connection(CA)
    assert not (c.data_dir / "tenants" / str(A)).exists() and bpath.is_file()
    assert db.connection(CB) and db.one("SELECT count(*) n FROM messages")["n"] == 1
    assert TenantDB(db, B).meta("api_hash") == "hash222" and db.one("SELECT count(*) n FROM saved")["n"] == 1
    assert db.one("SELECT count(*) n FROM watches")["n"] == 1
    assert not db.one("SELECT user_id FROM account_meta WHERE user_id=?", (A,))
    await s.handle(event(A, CA, 32, "must not resurrect"))
    assert not db.one("SELECT id FROM accounts WHERE id=?", (A,))
    await s.handle(direct(A, "/start", 33))
    assert db.one("SELECT id FROM accounts WHERE id=?", (A,))
    assert not db.connection(CA)


async def test_ai_queue_fairness_and_input_cancel(tenants):
    db, s, tg, c = tenants
    for n, (uid, text) in enumerate(((A, "/ai A1"), (A, "/ai A2"), (B, "/ai B1")), 20):
        await s.handle(direct(uid, text, n))
    assert "LOCAL TEST ANSWER" not in texts(db, A)
    await run_one(s)
    await run_one(s)
    assert "LOCAL TEST ANSWER: A1" in texts(db, A) and "LOCAL TEST ANSWER: B1" in texts(db, B)
    assert "LOCAL TEST ANSWER: A2" not in texts(db, A)
    await run_one(s)
    assert "LOCAL TEST ANSWER: A2" in texts(db, A)
    for n, text in enumerate(("🤖 AI", "❌ Отмена", "not a question"), 30):
        await s.handle(direct(A, text, n))
    assert db.one("SELECT count(*) n FROM jobs WHERE tenant_id=?", (A,))["n"] == 2


async def test_busy_ai_does_not_block_other_users_or_archiving(tenants):
    db, s, tg, c = tenants
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await finish.wait()
        return "DONE"

    s.ai.answer = slow
    await s.handle(direct(A, "/ai expensive", 20))
    task = asyncio.create_task(run_one(s))
    await started.wait()
    await s.handle(event(B, CB, 21, "Still archived"))
    await s.handle(direct(B, "⚙️ Настройки", 22))
    assert db.history(CB, 999, 7)[0]["text"] == "Still archived"
    assert "Настройки" in texts(db, B)
    finish.set()
    await task


async def test_deleted_generation_cannot_deliver_late_ai_response(tenants):
    db, s, tg, c = tenants
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await finish.wait()
        return "LATE PRIVATE ANSWER"

    s.ai.answer = slow
    await s.handle(direct(A, "/ai secret", 20))
    task = asyncio.create_task(run_one(s))
    await started.wait()
    await s.handle(direct(A, "/delete_data", 21))
    nonce = TenantDB(db, A).meta("delete_nonce")
    await s.handle(cb(A, "erase:" + nonce, 22))
    await s.handle(direct(A, "/start", 23))
    finish.set()
    await task
    assert "LATE PRIVATE ANSWER" not in texts(db, A)


async def test_per_user_job_limits_do_not_block_other_account(tenants):
    db, s, *_ = tenants
    for n in range(20, 24):
        await s.handle(direct(A, "/ai Q" + str(n), n))
    await s.handle(direct(B, "/ai B", 30))
    assert db.one("SELECT count(*) n FROM jobs WHERE tenant_id=? AND state='pending'", (A,))["n"] == 2
    assert db.one("SELECT count(*) n FROM jobs WHERE tenant_id=? AND state='pending'", (B,))["n"] == 1
    assert "лимит" in texts(db, A) and "лимит" not in texts(db, B)


async def test_restart_retains_accounts_connections_meta_and_keyboard(tenants):
    db, s, tg, c = tenants
    TenantDB(db, A).set_meta("setting:edit_notifications", "0")
    await s.handle(event(A, CA, 20, "persistent"))
    other = DB(c.data_dir)
    root = Service(other, tg, FakeAI(), Media(other, tg, c), c)
    await root.handle(direct(A, "/start", 30))
    assert (
        other.history(CA, 999, 7)[0]["text"] == "persistent"
        and TenantDB(other, A).meta("setting:edit_notifications") == "0"
    )
    assert any(
        json.loads(r["payload"]).get("reply_markup", {}).get("is_persistent")
        for r in other.all("SELECT payload FROM outbox WHERE event_key LIKE '111:30:%'")
    )
    other.close()


def test_legacy_migration_preserves_history_and_moves_files(tmp_path):
    dbfile = tmp_path / "archive.sqlite3"
    c = sqlite3.connect(dbfile)
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    c.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at INTEGER)")
    for f in sorted(migrations.glob("00[123]_*.sql")):
        c.executescript(f.read_text())
        c.execute("INSERT INTO schema_migrations VALUES(?,?)", (f.name, now()))
    c.execute("INSERT INTO meta VALUES(?,?)", ("owner_id", str(A)))
    c.execute("INSERT INTO meta VALUES(?,?)", ("lang", "en"))
    c.execute(
        "INSERT INTO connections VALUES(?,?,?,?,?,?,?)",
        (CA, A, A, 1, '{"can_reply":true}', dump(connection(A, CA)), now()),
    )
    c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)", (CA, 999, 7, 999, now(), now(), now(), 1))
    for n, text in enumerate(("ORIGINAL", "EDITED")):
        c.execute(
            "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (n + 1, CA, 999, 7, n + 1, n, "original" if n == 0 else "edit", text, None, "{}", now(), None),
        )

    c.commit()
    c.close()
    old = tmp_path / "media"
    old.mkdir()
    file = old / "fixture.bin"
    file.write_bytes(b"preserved local file")
    c = sqlite3.connect(dbfile)
    c.execute(
        "INSERT INTO media(id,version_id,kind,file_id,file_unique_id,metadata,path,size,state) VALUES(1,1,?,?,?,?,?,?,?)",
        ("document", "f", "u", "{}", "media/fixture.bin", len(file.read_bytes()), "ready"),
    )
    c.commit()
    c.close()
    db = DB(tmp_path)
    migrate_storage(db, tmp_path)
    assert [v["text"] for v in db.history(CA, 999, 7)] == ["ORIGINAL", "EDITED"]
    assert TenantDB(db, A).meta("lang") == "en" and db.meta("owner_id") is None
    row = db.one("SELECT * FROM media")
    assert (tmp_path / row["path"]).read_bytes() == b"preserved local file"
    assert row["path"].startswith("tenants/111/media/")
    assert db.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    db.close()
    again = DB(tmp_path)
    migrate_storage(again, tmp_path)
    assert len(again.history(CA, 999, 7)) == 2
    again.close()


async def test_connection_owner_is_immutable_without_creating_foreign_account(tenants):
    db, s, *_ = tenants
    with pytest.raises(ValueError):
        db.put_connection(connection(333, CA))
    await s.handle({"update_id": 50, "business_connection": connection(333, CA)})
    assert db.connection(CA)["owner_id"] == A
    assert not db.one("SELECT id FROM accounts WHERE id=333")


async def test_command_cannot_target_foreign_connection_even_inside_worker(tenants):
    db, s, tg, c = tenants
    actor = Service(db, tg, s.ai, s.media, c, account_id=A)
    actor.job_mode = True
    await actor.command(event(B, CB)["business_message"], "stat", CB)
    assert "Сообщений" not in texts(db, A)
    assert not db.one(
        "SELECT payload FROM outbox WHERE tenant_id=? AND json_extract(payload,'$.business_connection_id')=?",
        (A, CB),
    )


@pytest.mark.parametrize(
    "route",
    ["sum:not-a-number", "sum:999999999999999999999999", "a:all:-999999", "m:999999999999999999999999"],
)
async def test_untrusted_callback_values_are_handled_without_worker_failure(tenants, route):
    db, s, *_ = tenants
    await s.handle(cb(A, route, 50))
    assert "B PRIVATE" not in texts(db, A)


async def test_inflight_delivery_after_erasure_cannot_recreate_account_metadata(tenants):
    db, s, tg, c = tenants
    actor = Service(db, tg, s.ai, s.media, c, account_id=A)
    actor.db.enqueue("inflight", "sendMessage", {"chat_id": A, "text": "card", "_ui_menu": True})
    row = db.one("SELECT * FROM outbox WHERE event_key='111:inflight'")
    started, finish = asyncio.Event(), asyncio.Event()

    class SlowTelegram:
        async def call(self, method, **payload):
            started.set()
            await finish.wait()
            return {"message_id": 987}

    task = asyncio.create_task(deliver(db, SlowTelegram(), c, row))
    await started.wait()
    from smotritel.privacy import erase

    await erase(actor)
    await s.handle(direct(A, "/start", 50))
    finish.set()
    await task
    assert TenantDB(db, A).meta("ui_message_id") != "987"
    assert not db.one("SELECT id FROM outbox WHERE event_key='111:inflight'")
    assert db.connection(CB)


async def test_ai_progress_is_deleted_and_result_keeps_reply_keyboard(tenants):
    db, s, tg, c = tenants
    await s.handle(direct(A, "/ai Hello", 50))
    job = db.one("SELECT payload FROM jobs")
    status = json.loads(job["payload"])["progress"]

    class Gateway:
        def __init__(self):
            self.calls = []

        async def call(self, method, **payload):
            if method == "editMessageText":
                assert not self.calls[0][1].get("reply_markup"), (
                    "Telegram cannot edit a Reply Keyboard message"
                )
            self.calls.append((method, payload))
            return {"message_id": 987}

    gateway = Gateway()
    await deliver(db, gateway, c, db.one("SELECT * FROM outbox WHERE event_key=?", (status,)))
    await run_one(s)
    res = db.one(
        "SELECT * FROM outbox WHERE tenant_id=? AND json_extract(payload,'$._replace_event')=?", (A, status)
    )
    await deliver(db, gateway, c, res)
    assert gateway.calls[0][0] == "sendMessage" and "reply_markup" not in gateway.calls[0][1]
    assert any(
        json.loads(r["payload"]).get("reply_markup", {}).get("is_persistent")
        for r in db.all("SELECT payload FROM outbox WHERE tenant_id=?", (A,))
    )
    assert gateway.calls[1][0] == "deleteMessage" and gateway.calls[1][1]["message_id"] == 987
    assert gateway.calls[2][0] == "sendMessage"
    assert "LOCAL TEST ANSWER: Hello" in gateway.calls[2][1]["text"]


async def test_home_restores_main_keyboard_from_more_and_restart(tenants):
    db, s, *_ = tenants

    await s.handle(direct(A, "➕ Ещё", 50))
    assert TenantDB(db, A).meta("keyboard_layout") == "more"
    await s.handle(direct(A, "🏠 Главная", 51))
    assert TenantDB(db, A).meta("keyboard_layout") == "main"
    db.set_meta("started_at", now() + 1)
    await s.handle(direct(A, "⚙️ Настройки", 52))
    rows = db.all("SELECT payload FROM outbox WHERE event_key LIKE '111:52:%'")
    assert all("inline_keyboard" not in json.loads(r["payload"]).get("reply_markup", {}) for r in rows)
    assert TenantDB(db, A).meta("lower_screen") == "settings"
    assert any(
        "Главная" in label
        for r in rows
        for row in json.loads(r["payload"]).get("reply_markup", {}).get("keyboard", [])
        for label in row
    )


async def test_per_user_interaction_limit_keeps_other_account_and_history_available(tenants):
    db, s, *_ = tenants
    db.run("INSERT OR REPLACE INTO usage_limits VALUES(?,'interaction',?,60)", (A, now() // 60))
    await s.handle(direct(A, "📊 Статистика", 50))
    await s.handle(direct(B, "📊 Статистика", 51))
    await s.handle(event(A, CA, 52, "History never throttled"))
    assert "Слишком много" in texts(db, A) and "Статистика архива" in texts(db, B)
    assert db.history(CA, 999, 7)[0]["text"] == "History never throttled"


@pytest.mark.parametrize("mode", ["ask", "translate", "weather", "search", "afk", "voice"])
async def test_all_input_states_cancel_on_main_navigation(tenants, mode):
    db, s, *_ = tenants
    TenantDB(db, A).set_meta("input_mode", mode)
    TenantDB(db, A).set_meta("keyboard_layout", "waiting")
    await s.handle(direct(A, "🏠 Главная", 50))
    assert not TenantDB(db, A).meta("input_mode")
    assert not db.one("SELECT * FROM jobs")
    assert TenantDB(db, A).meta("keyboard_layout") == "main"


async def test_direct_watch_namespace_isolates_same_peer_id(tenants):
    db, s, *_ = tenants
    for uid in (A, B):
        db.observe("dm:" + str(uid), {"id": 999, "first_name": "Private peer " + str(uid)})
        db.run("INSERT INTO watches VALUES(?,?)", ("dm:" + str(uid), 999))
    await s.handle(direct(A, "/search 999", 50))
    assert "Private peer 111" in texts(db, A) and "Private peer 222" not in texts(db, A)


async def test_confirmed_erasure_does_not_remove_unowned_legacy_files(tenants):
    db, s, tg, c = tenants
    folder = c.data_dir / "generated"
    folder.mkdir(exist_ok=True)
    orphan = folder / "unknown-owner.txt"
    orphan.write_text("unattributed legacy file")
    from smotritel.privacy import erase

    await erase(Service(db, tg, s.ai, s.media, c, account_id=A))
    assert orphan.exists() and db.connection(CB)


def test_legacy_dm_watch_settings_and_deletions_migrate_into_owner_namespace(tmp_path):
    conn = sqlite3.connect(tmp_path / "archive.sqlite3")
    conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at INTEGER)")
    for file in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("00[123]_*.sql")):
        conn.executescript(file.read_text())
        conn.execute("INSERT INTO schema_migrations VALUES(?,?)", (file.name, now()))
    conn.execute("INSERT INTO meta VALUES('owner_id','111')")
    conn.execute("INSERT INTO messages VALUES('dm',111,7,999,1,1,2,1)")
    conn.execute("INSERT INTO versions VALUES(1,'dm',111,7,1,0,'original','private DM',NULL,'{}',1,NULL)")
    conn.execute("INSERT INTO users VALUES('dm',999,'{}',1)")
    conn.execute("INSERT INTO user_history VALUES(1,'dm',999,'{}',1)")
    conn.execute("INSERT INTO watches VALUES('dm',999)")
    conn.execute("INSERT INTO saved VALUES('dm',111,7,1)")
    conn.execute("INSERT INTO deletions VALUES(2,'dm',111,7,2)")
    conn.execute("INSERT INTO chats VALUES('dm',111,'{}')")
    conn.execute("INSERT INTO chat_settings VALUES('dm',111,'{}')")
    conn.execute("INSERT INTO ui_actions VALUES(1,1)")
    conn.commit()
    conn.close()
    db = DB(tmp_path)
    for table in (
        "messages",
        "versions",
        "users",
        "user_history",
        "watches",
        "saved",
        "deletions",
        "chats",
        "chat_settings",
    ):
        assert db.one("SELECT connection_id FROM " + table)["connection_id"] == "dm:111"
    assert db.one("SELECT tenant_id FROM processed_actions")["tenant_id"] == A
    assert not db.one("SELECT name FROM sqlite_master WHERE name='ui_actions'")
    assert not db.all("PRAGMA foreign_key_check")
    assert db.history("dm:111", A, 7)[0]["text"] == "private DM"
    db.close()


@pytest.mark.parametrize(
    "previous_chat,previous_cid,target_chat,target_cid,edits",
    [
        (A, None, 999, CA, False),
        (999, CA, A, None, False),
        (999, CA, 998, CA, False),
        (999, CA, 999, CA, True),
    ],
)
async def test_progress_edit_requires_same_chat_and_business_context(
    tenants, previous_chat, previous_cid, target_chat, target_cid, edits
):
    db, s, tg, c = tenants
    actor = Service(db, tg, s.ai, s.media, c, account_id=A)
    actor.db.enqueue("prior-progress", "sendMessage", {"chat_id": previous_chat, "text": "status"})
    db.run(
        "UPDATE outbox SET state='sent',payload='{}',response_message_id=987,response_chat_id=?,response_connection_id=? WHERE event_key='111:prior-progress'",
        (previous_chat, previous_cid),
    )
    body = {"chat_id": target_chat, "text": "result", "_replace_event": "111:prior-progress"}
    if target_cid:
        body["business_connection_id"] = target_cid
    actor.db.enqueue("new-result", "sendMessage", body)
    await deliver(db, tg, c, db.one("SELECT * FROM outbox WHERE event_key='111:new-result'"))
    assert tg.calls[-1][0] == ("editMessageText" if edits else "sendMessage")
    assert not any(call[0] == "deleteMessage" for call in tg.calls)


async def test_business_ai_progress_and_result_share_recipient(tenants):
    db, s, tg, c = tenants
    await s.handle(event(A, CA, 50, ".ai Business question", sender=A))
    job = db.one("SELECT payload FROM jobs")
    key = json.loads(job["payload"])["progress"]
    status = db.one("SELECT payload FROM outbox WHERE event_key=?", (key,))
    status_body = json.loads(status["payload"])
    assert status_body["chat_id"] == 999 and status_body["business_connection_id"] == CA
    assert "reply_markup" not in status_body
    await run_one(s)
    res = db.one("SELECT payload FROM outbox WHERE json_extract(payload,'$._replace_event')=?", (key,))
    res_body = json.loads(res["payload"])
    assert res_body["chat_id"] == 999 and res_body["business_connection_id"] == CA
