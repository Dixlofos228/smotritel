# входящие события
from .tenancy import register
from .telegram import TelegramError
from .service import Service


async def route_update(root, update):
    db = root.base
    user = None
    if "business_connection" in update:
        b = update["business_connection"]
        user = b["user"]
        existing = db.connection(b["id"])
        if existing and existing["owner_id"] != user["id"]:
            return
        if db.one("SELECT id FROM revoked_connections WHERE id=?", (b["id"],)):
            if not b["is_enabled"]:
                return
            if not db.one("SELECT id FROM accounts WHERE id=?", (user["id"],)):
                return
            db.run("DELETE FROM revoked_connections WHERE id=?", (b["id"],))
        register(db, user)
    elif any(
        k in update for k in ("business_message", "edited_business_message", "deleted_business_messages")
    ):
        m = next(
            update[k]
            for k in ("business_message", "edited_business_message", "deleted_business_messages")
            if k in update
        )
        cid = m["business_connection_id"]
        if db.one("SELECT id FROM revoked_connections WHERE id=?", (cid,)):
            return
        conn = db.connection(cid)
        if not conn:
            try:
                b = await root.tg.call("getBusinessConnection", business_connection_id=cid)
            except TelegramError as error:
                if error.code in (400, 403, 404):
                    return
                raise
            db.put_connection(b)
            conn = db.connection(cid)
        user = {"id": conn["owner_id"]}
        if not db.one("SELECT id FROM accounts WHERE id=?", (user["id"],)):
            return
    elif "callback_query" in update:
        q = update["callback_query"]
        user = q.get("from", {})
        if q.get("message", {}).get("chat", {}).get("id") != user.get("id"):
            return
        if not db.one("SELECT id FROM accounts WHERE id=?", (user.get("id"),)):
            return
    elif "message" in update:
        m = update["message"]
        user = m.get("from", {})
        if (
            m.get("chat", {}).get("type") != "private"
            or m["chat"]["id"] != user.get("id")
            or user.get("is_bot")
        ):
            return
        if not db.one("SELECT id FROM accounts WHERE id=?", (user["id"],)):
            if not m.get("text", "").startswith("/start"):
                await root.tg.call(
                    "sendMessage",
                    chat_id=user["id"],
                    text="Нажмите /start, чтобы открыть свой аккаунт Смотрителя",
                )
                return
        register(db, user)
    else:
        return
    actor = Service(db, root.tg, root.ai, root.media, root.c, account_id=user["id"])
    actor.event = str(update["update_id"])
    if ("message" in update or "callback_query" in update) and not actor.interaction_allowed(
        update["update_id"]
    ):
        if "callback_query" in update:
            await root.tg.call(
                "answerCallbackQuery",
                callback_query_id=update["callback_query"]["id"],
                text="Попробуйте через минуту",
            )
        return
    await actor.handle(update)
