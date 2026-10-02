from smotritel.db import DB
from smotritel.config import Config
from smotritel.service import Service
from smotritel.media import Media
from conftest import CID, OWNER, PEER, business, connection, FakeTelegram, FakeAI, out_text


async def test_delete_edit_restart_e2e(tmp_path):
    c = Config(tmp_path, owner_id=OWNER)
    tg = FakeTelegram()
    db = DB(tmp_path)
    db.put_connection(connection())
    svc = Service(db, tg, FakeAI(), Media(db, tg, c), c, account_id=OWNER)
    await svc.handle(business(10, "TEST DELETE 12345", 7))
    await svc.handle(business(11, "TEST EDIT ORIGINAL", 8))
    db.close()
    db = DB(tmp_path)
    svc = Service(db, tg, FakeAI(), Media(db, tg, c), c, account_id=OWNER)
    await svc.handle(business(12, "TEST EDIT CHANGED", 8, edited=True))
    deleted = {
        "update_id": 13,
        "deleted_business_messages": {
            "business_connection_id": CID,
            "chat": {"id": PEER, "type": "private"},
            "message_ids": [7, 8],
        },
    }
    await svc.handle(deleted)
    await svc.handle(deleted)
    assert [v["text"] for v in db.history(CID, PEER, 8)] == ["TEST EDIT ORIGINAL", "TEST EDIT CHANGED"]
    assert db.one("SELECT deleted_at FROM messages WHERE message_id=7")["deleted_at"]
    text = out_text(db)
    assert "TEST DELETE 12345" in text and "Удалённое сообщение" in text
    assert "TEST EDIT ORIGINAL" in text and "TEST EDIT CHANGED" in text
    assert db.one("SELECT COUNT(*) AS n FROM deletions")["n"] == 2
    db.close()


async def test_duplicate_and_chat_scoping(system):
    db, s, *_ = system
    u = business()
    await s.handle(u)
    await s.handle(u)
    other = business(11, "Another chat")
    other["business_message"]["chat"]["id"] = 3003
    await s.handle(other)
    assert len(db.history(CID, PEER, 7)) == 1
    assert len(db.history(CID, 3003, 7)) == 1


async def test_unknown_delete_has_no_invented_original(system):
    db, s, *_ = system
    await s.handle(
        {
            "update_id": 5,
            "deleted_business_messages": {
                "business_connection_id": CID,
                "chat": {"id": PEER},
                "message_ids": [999],
            },
        }
    )
    assert not db.history(CID, PEER, 999)
    assert "Первоначальная версия не получена" in out_text(db)


async def test_edit_first_does_not_claim_original(system):
    db, s, *_ = system
    await s.handle(business(9, "First known edit", edited=True))
    assert db.one("SELECT original_observed FROM messages")["original_observed"] == 0
    assert "Первоначальная версия не получена" in out_text(db)


async def test_caption_media_all_kinds_and_restart(system):
    db, s, tg, c = system
    extra = {
        "text": None,
        "caption": "original caption",
        "photo": [{"file_id": "small", "file_unique_id": "s"}, {"file_id": "big", "file_unique_id": "b"}],
    }
    for kind in ("document", "video", "voice", "video_note", "sticker", "audio", "animation"):
        extra[kind] = {
            "file_id": kind,
            "file_unique_id": kind,
            "file_size": 100,
            "file_name": "../../unsafe-name",
        }
    await s.handle(business(20, mid=20, **extra))
    hist = db.history(CID, PEER, 20)
    rows = await s.media.ensure([hist[0]["id"]])
    assert len(rows) == 8 and all(r["state"] == "ready" for r in rows)
    assert "small" not in [r["file_id"] for r in rows]
    for r in rows:
        assert s.media.path(r).is_file()
        assert r["sha256"] and ".." not in r["path"]
    await s.handle(business(21, mid=20, edited=True, text=None, caption="changed caption"))
    assert [v["caption"] for v in db.history(CID, PEER, 20)] == ["original caption", "changed caption"]


def test_inbox_persisted_before_offset_and_rollback(system):
    db, *_ = system
    updates = [business(1), business(2)]
    db.ingest(updates)
    db.ingest(updates)
    assert db.meta("offset") == "3"
    assert db.one("SELECT COUNT(*) AS n FROM inbox")["n"] == 2
    try:
        with db.transaction():
            db.set_meta("offset", 55)
            raise RuntimeError("simulated disk commit failure")
    except RuntimeError:
        pass
    assert db.meta("offset") == "3"
    db.ingest([business(0)])
    assert db.meta("offset") == "1"


def test_migrations_idempotent(system):
    db, _, _, c = system
    other = DB(c.data_dir)
    from pathlib import Path

    want = len(list((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")))
    assert len(other.all("SELECT * FROM schema_migrations")) == want
    assert other.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    other.close()


async def test_tombstone_then_original_retains_sender_time(system):
    db, s, *_ = system
    await s.handle(
        {
            "update_id": 5,
            "deleted_business_messages": {
                "business_connection_id": CID,
                "chat": {"id": PEER},
                "message_ids": [7],
            },
        }
    )
    await s.handle(business(6, "Late original", 7))
    row = db.one("SELECT * FROM messages WHERE message_id=7")
    assert row["sender_id"] == PEER and row["sent_at"] and row["deleted_at"]
    assert db.history(CID, PEER, 7)[0]["text"] == "Late original"
