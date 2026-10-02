# аккаунты
import json
import hashlib
import shutil
import uuid
from pathlib import Path
from .db import now, dump

GLOBALS = {"runtime_status", "local_health", "bot_username", "bot_id", "started_at", "branding_version"}


def register(db, user):
    uid = int(user["id"])
    db.run(
        "INSERT INTO accounts VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile,updated_at=excluded.updated_at",
        (uid, dump(user), now(), now(), uuid.uuid4().hex),
    )
    return uid


def owner_of(db, cid, chat):
    if cid.startswith("dm:"):
        try:
            uid = int(cid[3:])
        except ValueError:
            return None
        return uid if uid == int(chat) and db.one("SELECT id FROM accounts WHERE id=?", (uid,)) else None
    row = db.connection(cid)
    return row["owner_id"] if row else None


# sql сам проверяет владельца
class TenantDB:
    def __init__(self, db, uid):
        self.base, self.uid = db, int(uid)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def meta(self, key, default=None):
        if key in GLOBALS:
            return self.base.meta(key, default)
        if key in ("owner_profile", "profile"):
            a = self.one("SELECT profile FROM accounts WHERE id=?", (self.uid,))
            return a["profile"] if a else default
        row = self.one("SELECT value FROM account_meta WHERE user_id=? AND key=?", (self.uid, key))
        return row["value"] if row else default

    def set_meta(self, key, value):
        if key in GLOBALS:
            raise ValueError("User cannot set server metadata")
        if key in ("owner_profile", "profile"):
            self.run("UPDATE accounts SET profile=?,updated_at=? WHERE id=?", (str(value), now(), self.uid))
            return
        self.run(
            "INSERT INTO account_meta VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (self.uid, key, str(value)),
        )

    def enqueue(self, key, method, payload):
        acct = self.one("SELECT generation FROM accounts WHERE id=?", (self.uid,))
        if not acct:
            return
        payload = {**payload, "_generation": acct["generation"]}
        self.run(
            "INSERT OR IGNORE INTO outbox(event_key,method,payload,tenant_id) VALUES(?,?,?,?)",
            (f"{self.uid}:{key}", method, dump(payload), self.uid),
        )

    def event_key(self, key):
        return f"{self.uid}:{key}"

    def owns(self, cid, chat):
        return owner_of(self.base, cid, chat) == self.uid


def migrate_storage(db, root):
    root = Path(root).resolve()
    for row in db.all(
        "SELECT f.*,v.connection_id,v.chat_id FROM media f JOIN versions v ON v.id=f.version_id WHERE f.path IS NOT NULL"
    ):
        uid = owner_of(db, row["connection_id"], row["chat_id"])
        if not uid:
            raise ValueError("Media without a known tenant")
        old = (root / row["path"]).resolve()
        if not old.is_relative_to(root):
            raise ValueError("Unsafe legacy media path")
        dst = root / "tenants" / str(uid) / "media" / old.name
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if old != dst:
            if old.is_file():
                shutil.copy2(old, dst)
                dst.chmod(0o600)
            elif not dst.is_file():
                db.run("UPDATE media SET state='failed',path=NULL WHERE id=?", (row["id"],))
                continue
            if row.get("sha256"):
                with dst.open("rb") as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != row["sha256"]:
                        raise ValueError("Migrated media checksum mismatch")
            db.run("UPDATE media SET path=? WHERE id=?", (str(dst.relative_to(root)), row["id"]))
            if not db.one("SELECT id FROM media WHERE path=?", (row["path"],)):
                old.unlink(missing_ok=True)

    for row in db.all("SELECT * FROM outbox WHERE state='pending'"):
        p = json.loads(row["payload"])
        uid = row["tenant_id"]
        if not uid or not db.one("SELECT id FROM accounts WHERE id=?", (uid,)):
            db.run("UPDATE outbox SET state='failed',payload='{}' WHERE id=?", (row["id"],))
            continue
        if p.get("_file"):
            old = Path(p["_file"]).resolve()
            assets = Path(__file__).resolve().parents[1] / "assets"
            if old.is_relative_to(root) and not old.is_relative_to(root / "tenants" / str(uid)):
                dst = root / "tenants" / str(uid) / "generated" / old.name
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if old.is_file():
                    shutil.copy2(old, dst)
                    dst.chmod(0o600)
                p["_file"] = str(dst)
                db.run("UPDATE outbox SET payload=? WHERE id=?", (dump(p), row["id"]))
            elif not (old.is_relative_to(root / "tenants" / str(uid)) or old.is_relative_to(assets)):
                db.run("UPDATE outbox SET state='failed',payload='{}' WHERE id=?", (row["id"],))

    owners = db.all("SELECT DISTINCT owner_id FROM connections")
    if len(owners) == 1:
        uid = owners[0]["owner_id"]
        for old in (root / "generated").glob("*"):
            if old.is_file():
                dest = root / "tenants" / str(uid) / "generated" / old.name
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not dest.exists():
                    shutil.copy2(old, dest)
                dest.chmod(0o600)
                old.unlink()
