# отправка
import json
from pathlib import Path
from .telegram import TelegramError
from .tenancy import TenantDB

BUSINESS_RIGHTS = {
    "postStory": "can_manage_stories",
    "setBusinessAccountName": "can_change_name",
    "setBusinessAccountBio": "can_change_bio",
}


def business_allowed(db, uid, cid, method):
    conn = db.connection(cid)
    return bool(
        conn
        and conn["owner_id"] == uid
        and conn["enabled"]
        and json.loads(conn["rights"]).get(BUSINESS_RIGHTS.get(method, "can_reply"))
    )


def record_failure(db, row):
    body = json.loads(row["payload"])
    uid = row["tenant_id"]
    acct = db.one("SELECT generation FROM accounts WHERE id=?", (uid,))
    if not acct or body.get("_generation") != acct["generation"]:
        return
    tenant = TenantDB(db, uid)
    if body.get("_story_nonce"):
        from .stories import receipt

        receipt(tenant, body["_story_nonce"], body["_story_index"], False)
    if body.get("_clock_key"):
        key = "clock:" + body["_clock_key"]
        clock = json.loads(tenant.meta(key, "{}") or "{}")
        clock["enabled"] = False
        tenant.set_meta(key, json.dumps(clock))


async def deliver(db, tg, config, row):
    db = db.base if isinstance(db, TenantDB) else db
    body = json.loads(row["payload"])
    uid = row.get("tenant_id")
    acct = db.one("SELECT generation FROM accounts WHERE id=?", (uid,))
    if not acct:
        raise ValueError("Account no longer exists")
    gen = body.pop("_generation", None)
    if gen and gen != acct["generation"]:
        raise ValueError("Account generation changed")
    cid = body.get("business_connection_id")
    if cid:
        if not business_allowed(db, uid, cid, row["method"]):
            raise ValueError("Business recipient rejected")
    elif row["method"] != "answerCallbackQuery" and body.get("chat_id") != uid:
        raise ValueError("Recipient belongs to another account")
    tenant = TenantDB(db, uid)
    ui_menu = body.pop("_ui_menu", False)
    ui_key = body.pop("_ui_hash_key", None)
    digest = body.pop("_ui_digest", None)
    replacement = body.pop("_replace_event", None)
    fname = body.pop("_filename", None)
    story_nonce = body.pop("_story_nonce", None)
    story_index = body.pop("_story_index", None)
    body.pop("_clock_key", None)
    method = row["method"]
    deletion = body.pop("_delete_event", None)
    lower_keyboard = body.pop("_lower_keyboard", False)
    if deletion or replacement:
        ref = deletion or replacement
        prev = db.one("SELECT * FROM outbox WHERE event_key=? AND tenant_id=?", (ref, uid))
        if prev and prev["state"] == "pending":
            pending = json.loads(prev["payload"])
            if pending.get("chat_id") == body.get("chat_id") and pending.get("business_connection_id") == cid:
                db.run("UPDATE outbox SET state='superseded',payload='{}' WHERE id=?", (prev["id"],))
        elif (
            prev
            and prev["state"] == "sent"
            and prev["response_message_id"]
            and prev["response_chat_id"] == body.get("chat_id")
            and prev["response_connection_id"] == cid
        ):
            try:
                if cid and replacement and method == "sendMessage":
                    body["message_id"] = prev["response_message_id"]
                    method = "editMessageText"
                elif cid:
                    rights = json.loads(db.connection(cid)["rights"])
                    if rights.get("can_delete_sent_messages") or rights.get("can_delete_all_messages"):
                        await tg.call(
                            "deleteBusinessMessages",
                            business_connection_id=cid,
                            message_ids=[prev["response_message_id"]],
                        )
                else:
                    await tg.call(
                        "deleteMessage",
                        chat_id=body["chat_id"],
                        message_id=prev["response_message_id"],
                    )
            except TelegramError as error:
                if error.code not in (400, 403, 404):
                    raise
        if deletion:
            db.run(
                "UPDATE outbox SET state='sent',payload='{}',response_chat_id=? WHERE id=?", (uid, row["id"])
            )
            return True

        cur = db.one("SELECT generation FROM accounts WHERE id=?", (uid,))
        if not cur or cur["generation"] != acct["generation"]:
            return False
    if lower_keyboard and cid is None:
        from .navigation import reply_keyboard

        markup = tenant.meta("lower_markup")
        body["reply_markup"] = json.loads(markup) if markup else reply_keyboard(tenant.meta("lang", "ru"))
    if "_file" in body:
        file = Path(body.pop("_file")).resolve()
        field = body.pop("_field")
        assets = (Path(__file__).resolve().parents[1] / "assets").resolve()
        root = (config.data_dir / "tenants" / str(uid)).resolve()
        if not (file.is_relative_to(root) or file.is_relative_to(assets)):
            raise ValueError("Invalid tenant upload path")
        res = (
            await tg.upload(method, body, file, field, filename=fname)
            if fname
            else await tg.upload(method, body, file, field)
        )
    else:
        try:
            res = await tg.call(method, **body)
        except TelegramError as error:
            if error.not_modified:
                res = True
            elif error.edit_missing and method == "editMessageText":
                body.pop("message_id", None)
                res = await tg.call("sendMessage", **body)
            else:
                raise

    cur = db.one("SELECT generation FROM accounts WHERE id=?", (uid,))
    if not cur or cur["generation"] != acct["generation"]:
        return res
    with db.transaction():
        if story_nonce:
            from .stories import receipt

            receipt(tenant, story_nonce, story_index, True)
        if ui_key and digest:
            tenant.set_meta(ui_key, digest)
        mid = res.get("message_id") if isinstance(res, dict) else body.get("message_id")
        if ui_menu and mid:
            tenant.set_meta("ui_message_id", mid)
        db.run(
            "UPDATE outbox SET state='sent',payload='{}',response_message_id=?,response_chat_id=?,response_connection_id=? WHERE id=?",
            (mid, body.get("chat_id"), cid, row["id"]),
        )
    return res
