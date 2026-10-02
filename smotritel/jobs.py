# очередь
import json
import logging
from .db import now
from .service import Service


async def run_one(root):
    db = root.base
    row = db.one(
        """SELECT j.* FROM jobs j WHERE j.state='pending' ORDER BY coalesce((SELECT CAST(value AS INTEGER) FROM account_meta WHERE user_id=j.tenant_id AND key='last_job_at'),0),j.created_at,j.id LIMIT 1"""
    )
    if not row:
        return False
    body = json.loads(row["payload"])
    acct = db.one("SELECT generation FROM accounts WHERE id=?", (row["tenant_id"],))
    if not acct or acct["generation"] != body.get("generation"):
        db.run("DELETE FROM jobs WHERE id=?", (row["id"],))
        return True
    actor = Service(db, root.tg, root.ai, root.media, root.c, account_id=row["tenant_id"])
    actor.event = f"{row['update_id']}:job"
    actor.update_id = row["update_id"]
    actor.job_mode = True
    actor.nav_rev = body.get("navigation_revision")
    actor.progress = body.get("progress")
    db.run("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
    actor.db.set_meta("last_job_at", now())
    try:
        if row["kind"] == "command":
            await actor.command(
                body["message"], body["text"], body.get("cid"), private=body.get("private", False)
            )
        elif row["kind"] == "ui":
            await actor.ui.navigate(body["route"])
        elif row["kind"] == "auto":
            await actor.autoreply(body["message"], body["connection"])
        if actor.active():
            if actor.progress:
                actor.send(
                    body["message"]["chat"]["id"]
                    if body.get("cid") and not body.get("private")
                    else actor.owner,
                    "Готово",
                    None if body.get("private") else body.get("cid"),
                )
            db.run("UPDATE jobs SET state='done',payload='{}' WHERE id=?", (row["id"],))
    except Exception:
        logging.getLogger("smotritel").exception("Queued operation failed")
        if actor.active():
            actor.send(
                body["message"]["chat"]["id"] if body.get("cid") and not body.get("private") else actor.owner,
                "Не удалось завершить действие Попробуйте ещё раз позже",
                None if body.get("private") else body.get("cid"),
            )
            db.run("UPDATE jobs SET state='failed',payload='{}' WHERE id=?", (row["id"],))
    return True
