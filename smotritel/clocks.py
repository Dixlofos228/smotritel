# часы в имени и bio
import asyncio
import json
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from .db import now, dump
from .tenancy import TenantDB
from .telegram import TelegramError

METHODS = {
    "timename": ("setBusinessAccountName", "can_change_name"),
    "timebio": ("setBusinessAccountBio", "can_change_bio"),
}


def enqueue(tenant, cid, key, clock, stamp, restore=False):
    method, _ = METHODS[key]
    body = {"business_connection_id": cid, "_clock_key": key}
    if key == "timename":
        body.update(
            first_name=clock["first_name"] if restore else clock["first_name"][:54] + " · " + stamp,
            last_name=clock.get("last_name", ""),
        )
    else:
        body["bio"] = (
            clock["bio"] if restore else clock["bio"][:131] + (" · " if clock["bio"] else "") + stamp
        )
    tenant.run(
        "UPDATE outbox SET state='superseded',payload='{}' WHERE tenant_id=? AND method=? AND state='pending' AND json_extract(payload,'$._clock_key')=?",
        (tenant.uid, method, key),
    )
    tenant.enqueue(f"clock:{key}:{'restore' if restore else 'tick'}:{secrets.token_hex(6)}", method, body)


async def toggle(s, key, conn, arg=""):
    if key not in METHODS or arg not in ("", "on", "off"):
        raise ValueError("Invalid clock command")
    if s.db.one(
        "SELECT update_id FROM processed_actions WHERE tenant_id=? AND update_id=?", (s.owner, s.update_id)
    ):
        return
    method, right = METHODS[key]
    if conn["owner_id"] != s.owner or not conn["enabled"] or not json.loads(conn["rights"]).get(right):
        s.send(
            s.owner,
            "Для этой функции разрешите изменение "
            + ("имени" if key == "timename" else "bio")
            + " в Telegram → Business → Чат-боты",
        )
        return
    clock = json.loads(s.db.meta("clock:" + key, "{}") or "{}")
    enabled = arg == "on" if arg else not clock.get("enabled")
    if enabled and not clock.get("enabled"):
        prof = json.loads(conn["payload"])["user"]
        if key == "timebio":
            try:
                full = await asyncio.wait_for(s.tg.call("getChat", chat_id=s.owner), 5)
            except (TelegramError, TimeoutError):
                s.send(s.owner, "Не удалось получить исходный bio Попробуйте позже")
                return
            if not isinstance(full, dict) or full.get("id") != s.owner:
                raise ValueError("Business profile unavailable")
            clock = {"bio": full.get("bio", "")}
        else:
            clock = {"first_name": prof["first_name"], "last_name": prof.get("last_name", "")}
        clock.update(cid=conn["id"], enabled=True, last_tick=0)
    elif not enabled:
        if not clock.get("enabled") or clock.get("cid") != conn["id"]:
            s.send(s.owner, "Часы уже выключены")
            return
        enqueue(s.db, conn["id"], key, clock, "", restore=True)
        clock["enabled"] = False
    elif clock.get("cid") != conn["id"]:
        raise ValueError("Clock already uses another connection")
    if not s.active():
        return
    s.db.set_meta("clock:" + key, dump(clock))
    s.db.run("INSERT INTO processed_actions VALUES(?,?,?)", (s.owner, s.update_id, now()))
    tick(s.base)
    s.send(
        s.owner,
        "🕒 "
        + (
            "Обновление времени включено"
            if enabled
            else "Часы выключены; восстановление исходного текста отправлено в Telegram"
        )
        + "\nЧасовой пояс: "
        + s.db.meta("timezone", s.c.timezone),
    )


def tick(db):
    minute = now() // 60
    for row in db.all("SELECT * FROM account_meta WHERE key IN ('clock:timename','clock:timebio')"):
        clock = json.loads(row["value"])
        key = row["key"][6:]
        if not clock.get("enabled") or clock.get("last_tick") == minute:
            continue
        conn = db.connection(clock["cid"])
        tenant = TenantDB(db, row["user_id"])
        if (
            not conn
            or conn["owner_id"] != row["user_id"]
            or not conn["enabled"]
            or not json.loads(conn["rights"]).get(METHODS[key][1])
        ):
            clock["enabled"] = False
            tenant.set_meta(row["key"], dump(clock))
            continue
        stamp = datetime.fromtimestamp(
            now(), ZoneInfo(tenant.meta("timezone", "America/Montevideo"))
        ).strftime("%H:%M")
        enqueue(tenant, conn["id"], key, clock, stamp)
        clock["last_tick"] = minute
        tenant.set_meta(row["key"], dump(clock))
