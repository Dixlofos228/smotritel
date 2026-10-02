# архив
import json
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def now():
    return int(time.time())


class DB:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / "archive.sqlite3"
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA secure_delete=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at INTEGER)"
        )
        for file in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
            if not self.one("SELECT version FROM schema_migrations WHERE version=?", (file.name,)):
                self.conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + file.read_text()
                    + f"\nINSERT INTO schema_migrations VALUES('{file.name}',{now()});\nCOMMIT;"
                )
        self.path.chmod(0o600)

    def one(self, sql, args=()):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def run(self, sql, args=()):
        return self.conn.execute(sql, args)

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.run(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def ingest(self, updates):
        if not updates:
            return
        with self.transaction():
            for u in updates:
                self.run(
                    "INSERT OR IGNORE INTO inbox(update_id,payload,received_at) VALUES(?,?,?)",
                    (u["update_id"], dump(u), now()),
                )

            self.set_meta("offset", updates[-1]["update_id"] + 1)

    def enqueue(self, key, method, payload):
        self.run(
            "INSERT OR IGNORE INTO outbox(event_key,method,payload) VALUES(?,?,?)",
            (key, method, dump(payload)),
        )

    def connection(self, cid):
        return self.one("SELECT * FROM connections WHERE id=?", (cid,))

    def put_connection(self, b):
        from .tenancy import register

        existing = self.connection(b["id"])
        if existing and existing["owner_id"] != b["user"]["id"]:
            raise ValueError("Connection ownership cannot change")
        register(self, b["user"])
        self.run(
            "INSERT INTO connections VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "owner_id=excluded.owner_id, owner_chat_id=excluded.owner_chat_id, enabled=excluded.enabled, "
            "rights=excluded.rights, payload=excluded.payload, updated_at=excluded.updated_at",
            (
                b["id"],
                b["user"]["id"],
                b["user_chat_id"],
                int(b["is_enabled"]),
                dump(b.get("rights", {"can_reply": b.get("can_reply", False)})),
                dump(b),
                now(),
            ),
        )

    def observe(self, cid, user):
        if not user or not user.get("id"):
            return False
        prof = {
            k: user[k] for k in ("id", "first_name", "last_name", "username", "bio", "is_bot") if k in user
        }
        prev = self.one("SELECT profile FROM users WHERE connection_id=? AND id=?", (cid, user["id"]))

        if prev:
            old = json.loads(prev["profile"])
            if "bio" not in prof and "bio" in old:
                prof["bio"] = old["bio"]
        packed = dump(prof)
        changed = not prev or prev["profile"] != packed
        if changed:
            self.run(
                "INSERT INTO user_history(connection_id,user_id,profile,observed_at) VALUES(?,?,?,?)",
                (cid, user["id"], packed, now()),
            )
        self.run(
            "INSERT INTO users VALUES(?,?,?,?) ON CONFLICT(connection_id,id) DO UPDATE SET "
            "profile=excluded.profile, observed_at=excluded.observed_at",
            (cid, user["id"], packed, now()),
        )
        return bool(prev and changed)

    def archive(self, message, update_id, edited=False, reply=False):
        cid, chat, mid = message["business_connection_id"], message["chat"], message["message_id"]
        self.run(
            "INSERT INTO chats VALUES(?,?,?) ON CONFLICT(connection_id,id) DO UPDATE SET metadata=excluded.metadata",
            (cid, chat["id"], dump(chat)),
        )
        sender = message.get("from", {})
        self.observe(cid, sender)
        self.run(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,NULL,?) ON CONFLICT(connection_id,chat_id,message_id) "
            "DO UPDATE SET sender_id=COALESCE(messages.sender_id,excluded.sender_id), sent_at=COALESCE(messages.sent_at,excluded.sent_at), original_observed=MAX(original_observed,excluded.original_observed)",
            (
                cid,
                chat["id"],
                mid,
                sender.get("id"),
                message.get("date"),
                now(),
                int(not edited and not reply),
            ),
        )
        if self.one(
            "SELECT id FROM versions WHERE connection_id=? AND chat_id=? AND message_id=? AND update_id=?",
            (cid, chat["id"], mid, update_id),
        ):
            return None
        count = self.one(
            "SELECT COUNT(*) AS n FROM versions WHERE connection_id=? AND chat_id=? AND message_id=?",
            (cid, chat["id"], mid),
        )["n"]
        kind = "reply_snapshot" if reply else "edit" if edited else "original"
        v = self.run(
            "INSERT INTO versions(connection_id,chat_id,message_id,update_id,sequence,kind,text,caption,payload,observed_at,telegram_edit_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                chat["id"],
                mid,
                update_id,
                count,
                kind,
                message.get("text"),
                message.get("caption"),
                dump(message),
                now(),
                message.get("edit_date"),
            ),
        ).lastrowid
        for media_kind, meta in self.media_parts(message):
            self.run(
                "INSERT OR IGNORE INTO media(version_id,kind,file_id,file_unique_id,metadata,size) VALUES(?,?,?,?,?,?)",
                (
                    v,
                    media_kind,
                    meta["file_id"],
                    meta.get("file_unique_id", meta["file_id"]),
                    dump(meta),
                    meta.get("file_size"),
                ),
            )
        return v

    @staticmethod
    def media_parts(m):
        if m.get("photo"):
            yield "photo", m["photo"][-1]
        for kind in ("video", "document", "voice", "video_note", "sticker", "audio", "animation"):
            if m.get(kind):
                yield kind, m[kind]
        if m.get("live_photo"):
            p = m["live_photo"]
            if p.get("photo"):
                yield "live_photo_image", p["photo"][-1]
            if p.get("video"):
                yield "live_photo_video", p["video"]

    def history(self, cid, chat, mid):
        return self.all(
            "SELECT * FROM versions WHERE connection_id=? AND chat_id=? AND message_id=? ORDER BY sequence",
            (cid, chat, mid),
        )

    def settings(self, cid, chat):
        row = self.one("SELECT settings FROM chat_settings WHERE connection_id=? AND chat_id=?", (cid, chat))
        return json.loads(row["settings"]) if row else {}

    def set_settings(self, cid, chat, settings):
        self.run(
            "INSERT INTO chat_settings VALUES(?,?,?) ON CONFLICT(connection_id,chat_id) DO UPDATE SET settings=excluded.settings",
            (cid, chat, dump(settings)),
        )

    def stats(self, cid, chat, days=30):
        since = now() - days * 86400
        base = (cid, chat, since)
        res = self.one(
            "SELECT COUNT(*) AS messages, SUM(deleted_at IS NOT NULL) AS deleted, "
            "COUNT(DISTINCT sender_id) AS people FROM messages WHERE connection_id=? AND chat_id=? AND sent_at>=?",
            base,
        )
        res["edits"] = self.one(
            "SELECT COUNT(*) AS n FROM versions WHERE connection_id=? AND chat_id=? AND kind='edit' AND observed_at>=?",
            base,
        )["n"]
        res["top"] = self.all(
            "SELECT sender_id, COUNT(*) AS n FROM messages WHERE connection_id=? AND chat_id=? AND sent_at>=? "
            "GROUP BY sender_id ORDER BY n DESC LIMIT 5",
            base,
        )
        return res

    def close(self):
        self.conn.close()
