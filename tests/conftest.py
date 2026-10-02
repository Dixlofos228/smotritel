import json
import pytest
from smotritel.config import Config
from smotritel.db import DB, now
from smotritel.service import Service
from smotritel.media import Media

OWNER = 1001
PEER = 2002
CID = "test-business"


class FakeTelegram:
    def __init__(self):
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))
        if method == "getBusinessConnection":
            return connection()
        return True

    async def download(self, file_id, target, limit):
        target.write_bytes(b"fixture media " + file_id.encode())
        return {"file_path": "voice/file.ogg"}


class FakeAI:
    async def answer(self, prompt, system=""):
        return "LOCAL TEST ANSWER: " + prompt[:50]

    async def transcript(self, path):
        return "Fixture speech transcript"


def connection(owner=OWNER, cid=CID):
    return {
        "id": cid,
        "user": {"id": owner, "first_name": "Owner"},
        "user_chat_id": owner,
        "date": now(),
        "is_enabled": True,
        "rights": {"can_reply": True},
    }


def business(update_id=10, text="Hello", mid=7, owner=False, edited=False, **extra):
    sender = OWNER if owner else PEER
    m = {
        "business_connection_id": CID,
        "message_id": mid,
        "date": now(),
        "chat": {"id": PEER, "type": "private", "first_name": "Peer"},
        "from": {
            "id": sender,
            "first_name": "Owner" if owner else "Peer",
            "username": "peer" if not owner else "owner",
        },
        "text": text,
        **extra,
    }
    if edited:
        m["edit_date"] = now()
    return {"update_id": update_id, "edited_business_message" if edited else "business_message": m}


def direct(uid=OWNER, text="/start", update_id=1, **extra):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": now(),
            "from": {"id": uid, "first_name": "Test"},
            "chat": {"id": uid, "type": "private"},
            "text": text,
            **extra,
        },
    }


@pytest.fixture
def system(tmp_path):
    c = Config(tmp_path, owner_id=OWNER, pairing_code="local-pairing-secret", test_chat=PEER)
    db = DB(tmp_path)
    tg = FakeTelegram()
    db.put_connection(connection())
    svc = Service(db, tg, FakeAI(), Media(db, tg, c), c, account_id=OWNER)
    svc.job_mode = True
    svc.db.set_meta("ui_message_id", 55)
    yield db, svc, tg, c
    db.close()


def out_text(db):
    return "\n".join(json.loads(r["payload"]).get("text", "") for r in db.all("SELECT payload FROM outbox"))
