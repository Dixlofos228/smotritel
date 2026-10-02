import hashlib
import aiohttp
from aiohttp import web
from PIL import Image
from smotritel.api import create_app
from smotritel.telegram import TelegramError
from conftest import business, CID, PEER


async def test_real_local_http_api_auth_and_history(system):
    db, s, *_ = system
    await s.handle(business(10, "private data"))
    s.db.set_meta("api_hash", hashlib.sha256(b"local-secret").hexdigest())
    runner = web.AppRunner(create_app(db), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(url + "/archive") as r:
                assert r.status == 401 and "private data" not in await r.text()
            headers = {"Authorization": "Bearer local-secret"}
            async with client.get(url + f"/message/{CID}/{PEER}/7", headers=headers) as r:
                assert r.status == 200
                assert (await r.json())["versions"][0]["text"] == "private data"
                assert r.headers["Cache-Control"] == "no-store"
            async with client.get(url + "/archive?limit=invalid", headers=headers) as r:
                assert r.status == 400
            db.run("DELETE FROM account_meta WHERE user_id=? AND key='api_hash'", (s.owner,))
            async with client.get(url + "/archive", headers=headers) as r:
                assert r.status == 401
    finally:
        await runner.cleanup()


async def test_media_size_budget_and_retry(system):
    db, s, tg, c = system
    await s.handle(
        business(1, None, voice={"file_id": "v", "file_unique_id": "v", "file_size": c.max_media + 1})
    )
    row = db.one("SELECT * FROM media")
    await s.media.fetch(row)
    assert db.one("SELECT state FROM media")["state"] == "too_large"
    db.run("UPDATE media SET size=2,state='pending'")
    c.budget = 0
    await s.media.fetch(db.one("SELECT * FROM media"))
    assert db.one("SELECT state FROM media")["state"] == "budget_exceeded"
    c.budget = 100000000

    async def fail(*args):
        raise TelegramError(429, 10)

    tg.download = fail
    db.run("UPDATE media SET state='pending'")
    await s.media.fetch(db.one("SELECT * FROM media"))
    row = db.one("SELECT * FROM media")
    assert row["state"] == "pending" and row["attempts"] == 1 and row["next_attempt"] > 0
    assert not list(s.media.root.glob(".part-*"))


def test_story_grid_and_quote_are_real_images(system):
    _, s, _, c = system
    src = c.data_dir / "fixture.png"
    Image.new("RGB", (600, 300), "teal").save(src)
    paths = s.media.story(src)
    assert Image.open(paths[0]).size == (1080, 1920)
    grid = s.media.story(src, True)
    assert len(grid) == 9 and all(Image.open(p).size == (360, 360) for p in grid)
    quote = s.media.render_quote(["Проверка цитаты"], src)
    assert Image.open(quote).width == 900


async def test_command_and_worker_media_download_are_serialized(system):
    import asyncio

    db, s, tg, _ = system
    await s.handle(business(1, None, voice={"file_id": "v", "file_unique_id": "v"}))
    row = db.one("SELECT * FROM media")
    calls = 0
    orig = tg.download

    async def delayed(*args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return await orig(*args)

    tg.download = delayed
    await asyncio.gather(s.media.fetch(row), s.media.ensure([row["version_id"]]))
    assert calls == 1
    assert db.one("SELECT state FROM media")["state"] == "ready"


async def test_api_cursor_does_not_skip_messages_with_same_timestamp(system):
    db, s, *_ = system
    for i in range(1, 5):
        await s.handle(business(i, f"message {i}", i))
    db.run("UPDATE messages SET first_seen=100")
    s.db.set_meta("api_hash", hashlib.sha256(b"key").hexdigest())
    runner = web.AppRunner(create_app(db), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession(headers={"Authorization": "Bearer key"}) as client:
            async with client.get(f"http://127.0.0.1:{port}/archive?limit=2") as r:
                first = await r.json()
            async with client.get(
                f"http://127.0.0.1:{port}/archive?limit=2&before={first['next_cursor']}"
            ) as r:
                second = await r.json()
        assert {r["message_id"] for r in first["messages"] + second["messages"]} == {1, 2, 3, 4}
    finally:
        await runner.cleanup()


async def test_ai_does_not_follow_local_redirect_to_another_endpoint(system):
    from smotritel.ai import AI, AIUnavailable
    import pytest

    _, _, _, c = system
    followed = False

    async def redirect(request):
        raise web.HTTPTemporaryRedirect("/sink")

    async def sink(request):
        nonlocal followed
        followed = True
        return web.json_response({"message": {"content": "bad"}})

    app = web.Application()
    app.router.add_post("/api/chat", redirect)
    app.router.add_post("/sink", sink)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    c.ai_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        async with aiohttp.ClientSession() as client:
            with pytest.raises(AIUnavailable, match="307"):
                await AI(c, client).answer("synthetic private text")
        assert not followed
    finally:
        await runner.cleanup()
