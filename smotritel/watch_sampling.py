# профили
import asyncio
import json
from .db import now
from .service import Service
from .tenancy import TenantDB
from .telegram import TelegramError


async def sample(root):
    db = root.base
    rows = db.all(
        "SELECT w.connection_id,w.user_id,c.owner_id FROM watches w JOIN connections c ON c.id=w.connection_id WHERE c.enabled=1 ORDER BY coalesce((SELECT CAST(value AS INTEGER) FROM account_meta a WHERE a.user_id=c.owner_id AND a.key='bio_sample:'||w.connection_id||':'||w.user_id),0) LIMIT 10"
    )
    for row in rows:
        tenant = TenantDB(db, row["owner_id"])
        key = f"bio_sample:{row['connection_id']}:{row['user_id']}"
        if now() - int(tenant.meta(key, "0")) < 300:
            continue
        actor = Service(db, root.tg, root.ai, root.media, root.c, account_id=row["owner_id"])
        tenant.set_meta(key, now())
        try:
            full = await asyncio.wait_for(root.tg.call("getChat", chat_id=row["user_id"]), 3)
        except (TelegramError, TimeoutError):
            continue
        if not actor.active() or not db.one(
            "SELECT user_id FROM watches WHERE connection_id=? AND user_id=?",
            (row["connection_id"], row["user_id"]),
        ):
            continue
        if not isinstance(full, dict) or full.get("id") != row["user_id"] or full.get("type") != "private":
            continue
        before = db.one(
            "SELECT profile FROM users WHERE connection_id=? AND id=?", (row["connection_id"], row["user_id"])
        )
        if not before:
            continue
        prof = json.loads(before["profile"])
        updated = {**prof, "bio": full.get("bio", "")}
        for field in ("first_name", "last_name", "username"):
            if field in full:
                updated[field] = full[field]
            elif field in ("last_name", "username"):
                updated.pop(field, None)
        if db.observe(row["connection_id"], updated):
            actor.event = f"bio:{row['connection_id']}:{row['user_id']}:{now()}"
            actor.send(
                actor.owner,
                "👁 Изменение наблюдаемого профиля\n"
                + actor.profile_text(prof)
                + "\n→\n"
                + actor.profile_text(updated),
            )
