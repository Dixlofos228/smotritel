import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smotritel.db import DB
from smotritel.tenancy import TenantDB, migrate_storage
from restore import restore

TABLES = (
    "connections",
    "messages",
    "versions",
    "deletions",
    "saved",
    "users",
    "user_history",
    "watches",
    "chats",
    "chat_settings",
)


def rows(db, table):
    return [dict(r) for r in db.execute("SELECT * FROM " + table)]


def fingerprint(values):
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in values)


def verify(backup, parent):
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=parent, prefix="migration-") as temp:
        target = Path(temp)
        restore(backup, target)
        old = sqlite3.connect(target / "archive.sqlite3")
        old.row_factory = sqlite3.Row
        before = {t: rows(old, t) for t in TABLES}
        meta = {r["key"]: r["value"] for r in rows(old, "meta")}
        media = rows(old, "media")
        hashes = {
            r["id"]: hashlib.sha256((target / r["path"]).read_bytes()).hexdigest()
            for r in media
            if r["path"] and (target / r["path"]).is_file()
        }
        old.close()
        for table, values in before.items():
            for row in values:
                if row.get("connection_id") == "dm":
                    uid = row.get("chat_id") or (
                        row["id"]
                        if table == "chats"
                        else meta.get("owner_id") or row.get("id") or row.get("user_id")
                    )
                    row["connection_id"] = "dm:" + str(uid)
        db = DB(target)
        migrate_storage(db, target)
        for table in TABLES:
            assert fingerprint(before[table]) == fingerprint(db.all("SELECT * FROM " + table)), (
                table + " changed unexpectedly"
            )
        if meta.get("owner_id"):
            tenant = TenantDB(db, int(meta["owner_id"]))
            for key, value in meta.items():
                if key in (
                    "lang",
                    "plan",
                    "welcome_sent",
                    "ui_message_id",
                    "api_hash",
                    "archive_route",
                    "last_business_update",
                    "owner_profile",
                ) or key.startswith(("setting:", "ui_hash:")):
                    assert tenant.meta(key) == value, "Legacy setting was not preserved"
        for rid, digest in hashes.items():
            row = db.one("SELECT path FROM media WHERE id=?", (rid,))
            assert hashlib.sha256((target / row["path"]).read_bytes()).hexdigest() == digest
        assert db.one("PRAGMA integrity_check")["integrity_check"] == "ok"
        assert not db.all("PRAGMA foreign_key_check")
        want = {t: fingerprint(db.all("SELECT * FROM " + t)) for t in TABLES}
        count = db.one("SELECT count(*) n FROM accounts")["n"]
        db.close()
        db = DB(target)
        migrate_storage(db, target)
        assert all(want[t] == fingerprint(db.all("SELECT * FROM " + t)) for t in TABLES)
        db.close()
        print(
            json.dumps(
                {
                    "backup_restore": "PASS",
                    "migration": "PASS",
                    "repeat_migration_restart": "PASS",
                    "content_preserved": "PASS",
                    "media_checksums_preserved": len(hashes),
                    "integrity": "ok",
                    "foreign_keys": "PASS",
                    "accounts": count,
                    "preserved_rows": {t: len(v) for t, v in before.items()},
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    verify(args.backup, args.work_dir)
