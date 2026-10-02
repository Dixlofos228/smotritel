# удаление аккаунта
import json
import shutil
from .db import now


async def erase(service):
    db = service.base
    uid = service.uid
    root = service.c.data_dir.resolve()
    conns = [r["id"] for r in db.all("SELECT id FROM connections WHERE owner_id=?", (uid,))]
    with db.transaction():
        for cid in conns:
            db.run("INSERT OR REPLACE INTO revoked_connections VALUES(?,?)", (cid, now()))
            for table in (
                "messages",
                "saved",
                "watches",
                "chat_settings",
                "users",
                "user_history",
                "chats",
                "deletions",
            ):
                db.run(f"DELETE FROM {table} WHERE connection_id=?", (cid,))
            db.run("DELETE FROM connections WHERE id=?", (cid,))
        for table in (
            "messages",
            "saved",
            "chat_settings",
            "watches",
            "users",
            "user_history",
            "chats",
            "deletions",
        ):
            db.run(f"DELETE FROM {table} WHERE connection_id=?", ("dm:" + str(uid),))
        for row in db.all("SELECT update_id,payload FROM inbox WHERE state='pending'"):
            event = json.loads(row["payload"])
            direct = event.get("message", {})
            callback = event.get("callback_query", {})
            cid = next(
                (
                    event[k].get("business_connection_id")
                    for k in ("business_message", "edited_business_message", "deleted_business_messages")
                    if k in event
                ),
                None,
            )
            if (
                (direct.get("chat", {}).get("id") == uid and direct.get("from", {}).get("id") == uid)
                or callback.get("from", {}).get("id") == uid
                or cid in conns
            ):
                db.run("UPDATE inbox SET state='done',payload='{}' WHERE update_id=?", (row["update_id"],))
        db.run("DELETE FROM accounts WHERE id=?", (uid,))
    folder = (root / "tenants" / str(uid)).resolve()
    if folder.is_relative_to(root / "tenants"):
        shutil.rmtree(folder, ignore_errors=True)
    await service.tg.call(
        "sendMessage",
        chat_id=uid,
        text="Ваши данные удалены Отключите Смотрителя в Telegram Business, чтобы завершить подключение Для нового аккаунта нажмите /start",
        reply_markup={"remove_keyboard": True},
    )
