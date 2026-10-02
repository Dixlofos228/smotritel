# локальный api
import hashlib
from aiohttp import web
from .tenancy import TenantDB

SCOPE = "((m.connection_id='dm:'||? AND m.chat_id=?) OR EXISTS(SELECT 1 FROM connections c WHERE c.id=m.connection_id AND c.owner_id=?))"


def create_app(db):
    @web.middleware
    async def auth(request, handler):
        value = request.headers.get("Authorization", "")
        digest = hashlib.sha256(value[7:].encode()).hexdigest() if value.startswith("Bearer ") else ""
        acct = (
            db.one("SELECT user_id FROM account_meta WHERE key='api_hash' AND value=?", (digest,))
            if digest
            else None
        )
        if not acct:
            raise web.HTTPUnauthorized(text="Unauthorized")
        request["uid"] = acct["user_id"]
        resp = await handler(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    app = web.Application(middlewares=[auth], client_max_size=1024)

    async def archive(request):
        try:
            limit = min(100, max(1, int(request.query.get("limit", "20"))))
            before = min(9223372036854775807, max(0, int(request.query.get("before", "9223372036854775807"))))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid pagination") from None
        uid = request["uid"]
        rows = db.all(
            f"SELECT m.rowid AS cursor,m.* FROM messages m WHERE {SCOPE} AND m.rowid<? ORDER BY m.rowid DESC LIMIT ?",
            (uid, uid, uid, before, limit),
        )
        return web.json_response(
            {"messages": rows, "next_cursor": rows[-1]["cursor"] if len(rows) == limit else None}
        )

    async def message(request):
        try:
            args = (
                request.match_info["cid"],
                int(request.match_info["chat"]),
                int(request.match_info["mid"]),
            )
            if any(abs(value) > 9223372036854775807 for value in args[1:]):
                raise ValueError("ID too large")
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid ID") from None
        tenant = TenantDB(db, request["uid"])
        if not tenant.owns(args[0], args[1]):
            raise web.HTTPNotFound()
        row = db.one("SELECT * FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?", args)
        if not row:
            raise web.HTTPNotFound()
        return web.json_response({"message": row, "versions": db.history(*args)})

    async def health(request):
        uid = request["uid"]
        return web.json_response(
            {
                "registered": True,
                "connections": db.one("SELECT count(*) n FROM connections WHERE owner_id=?", (uid,))["n"],
                "pending_jobs": db.one(
                    "SELECT count(*) n FROM jobs WHERE tenant_id=? AND state IN ('pending','running')", (uid,)
                )["n"],
            }
        )

    app.router.add_get("/archive", archive)
    app.router.add_get("/message/{cid}/{chat}/{mid}", message)
    app.router.add_get("/health", health)
    return app
