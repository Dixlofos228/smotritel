import json
import pytest
from conftest import OWNER, direct, connection, business
from smotritel.service import Service
from smotritel.delivery import deliver
from smotritel.navigation import MAIN
from test_delivery import Gateway


def last(db):
    return db.one("SELECT * FROM outbox ORDER BY id DESC")


def choose(s, route):
    routes = json.loads(s.db.meta("lower_routes"))
    label = next(k for k, v in routes.items() if v == route)
    return label


async def test_settings_and_archive_actions_work_by_lower_text_buttons(system):
    db, s, _, _ = system
    await s.handle(direct(text="⚙️ Настройки", update_id=100))
    await s.handle(direct(text=choose(s, "toggle:edit_notifications"), update_id=101))
    assert s.db.meta("setting:edit_notifications") == "0"
    await s.handle(business(102, "A received original"))
    await s.handle(direct(text=choose(s, "menu"), update_id=103))
    await s.handle(direct(text="🗂 Архив", update_id=104))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    await s.handle(direct(text=choose(s, f"m:{rid}"), update_id=105))
    await s.handle(direct(text=choose(s, f"save:{rid}"), update_id=106))
    assert db.one("SELECT message_id FROM saved")["message_id"] == 7
    for r in db.all("SELECT payload FROM outbox"):
        assert "inline_keyboard" not in json.loads(r["payload"]).get("reply_markup", {})


@pytest.mark.parametrize("submit", ["2+2", "❌ Отмена", "⚙️ Настройки", "/start"])
async def test_prompt_is_deleted_after_submission_or_navigation(system, submit):
    db, s, _, c = system
    gateway = Gateway()
    await s.handle(direct(text="🤖 AI", update_id=100))
    prompt = last(db)
    await deliver(db, gateway, c, prompt)
    await s.handle(direct(text=submit, update_id=101))
    deletion = db.one("SELECT * FROM outbox WHERE method='deleteMessage' ORDER BY id DESC")
    await deliver(db, gateway, c, deletion)
    assert gateway.calls[-1] == ("deleteMessage", {"chat_id": OWNER, "message_id": 77})
    assert not s.db.meta("input_prompt") and not s.db.meta("input_mode")
    assert "Напишите вопрос" not in json.loads(last(db)["payload"]).get("text", "")


async def test_undelivered_prompt_is_superseded_without_api_delete(system):
    db, s, _, c = system
    await s.handle(direct(text="🤖 AI", update_id=100))
    prompt = last(db)
    await s.handle(direct(text="❌ Отмена", update_id=101))
    deletion = db.one("SELECT * FROM outbox WHERE method='deleteMessage'")
    gateway = Gateway()
    await deliver(db, gateway, c, deletion)
    assert not gateway.calls
    assert db.one("SELECT state,payload FROM outbox WHERE id=?", (prompt["id"],)) == {
        "state": "superseded",
        "payload": "{}",
    }


async def test_slow_result_keeps_latest_panel_when_delivered(system):
    db, s, _, c = system
    s.event = "slow"
    s.lower.main()
    s.send(OWNER, "A finished answer")
    answer = last(db)
    await s.handle(direct(text="➕ Ещё", update_id=100))
    cur = s.lower.current()
    assert len(cur["keyboard"]) > len(MAIN)
    gateway = Gateway()
    await deliver(db, gateway, c, answer)
    assert gateway.calls[-1][1]["reply_markup"] == cur
    assert s.db.meta("lower_screen") == "more"


async def test_late_job_card_cannot_rewind_navigation(system):
    db, s, _, c = system
    s.lower.main()
    actor = Service(db, s.tg, s.ai, s.media, c, account_id=OWNER)
    actor.job_mode = True
    actor.nav_rev = s.lower.revision()
    actor.event = "late"
    await s.handle(direct(text="⚙️ Настройки", update_id=100))
    cur = s.lower.current()
    revision = s.lower.revision()
    await actor.ui.show("a:all:0")
    assert s.lower.current() == cur and s.lower.revision() == revision
    await deliver(db, Gateway(), c, last(db))
    assert s.db.meta("lower_screen") == "settings"


async def test_keyboard_routes_and_prompt_refs_are_isolated_by_account(system):
    db, s, _, c = system
    db.put_connection(connection(owner=777, cid="other"))
    other = Service(db, s.tg, s.ai, s.media, c, account_id=777)
    s.lower.set([[{"text": "Same name", "callback_data": "settings"}]])
    other.lower.set([[{"text": "Same name", "callback_data": "profile"}]])
    assert s.lower.route("Same name") == "settings" and other.lower.route("Same name") == "profile"
    other.event = "prompt"
    other.prompt("Other account prompt")
    ref = other.db.meta("input_prompt")
    s.db.enqueue("forged-delete", "deleteMessage", {"chat_id": OWNER, "_delete_event": ref})
    gateway = Gateway()
    await deliver(db, gateway, c, last(db))
    assert not gateway.calls
    assert db.one("SELECT state FROM outbox WHERE event_key=?", (ref,))["state"] == "pending"


async def test_duplicate_archive_labels_select_distinct_actions(system):
    _, s, *_ = system
    s.lower.set([[{"text": "Same", "callback_data": "m:1"}, {"text": "Same", "callback_data": "m:2"}]])
    keys = s.lower.current()["keyboard"][0]
    assert len(set(keys)) == 2 and {s.lower.route(k) for k in keys} == {"m:1", "m:2"}


async def test_file_result_deletes_progress_without_extra_completion(system):
    db, s, _, c = system
    s.event = "file"
    gateway = Gateway()
    status = s.send(OWNER, "Preparing file", reply_menu=False)
    await deliver(db, gateway, c, last(db))
    s.progress = status
    path = s.media.generated / "fixture.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("An export")
    s.file(OWNER, path)

    async def upload(method, payload, file, field, filename=None):
        assert method == "sendDocument" and file == path
        assert payload["reply_markup"] == s.lower.current()
        return {"message_id": 88}

    gateway.upload = upload
    await deliver(db, gateway, c, last(db))
    assert gateway.calls[-1][0] == "deleteMessage" and s.progress is None


async def test_lower_summary_queues_work_instead_of_blocking_polling(system):
    db, s, _, c = system
    await s.handle(business(100, None, voice={"file_id": "voice", "file_unique_id": "voice"}))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    s.lower.set([[{"text": "Summary", "callback_data": f"sum:{rid}"}]])
    root = Service(db, s.tg, s.ai, s.media, c)
    await root.handle(direct(text="Summary", update_id=101))
    job = db.one("SELECT * FROM jobs")
    assert job["kind"] == "ui" and json.loads(job["payload"])["route"] == f"sum:{rid}"


async def test_uploaded_photo_actions_appear_in_lower_panel(system):
    db, s, _, c = system
    root = Service(db, s.tg, s.ai, s.media, c)
    await root.handle(direct(text="", update_id=100, photo=[{"file_id": "photo", "file_unique_id": "photo"}]))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    assert s.lower.route("Цитата") == f"perform:q:{rid}"
    assert s.lower.route("Stories 3×3") == f"perform:story:{rid}"
    assert s.db.meta("lower_screen") == "media-upload"


async def test_erasure_during_progress_deletion_prevents_result_request(system):
    db, s, _, c = system
    gateway = Gateway()
    s.event = "erase"
    status = s.send(OWNER, "Preparing", reply_menu=False)
    await deliver(db, gateway, c, last(db))
    s.progress = status
    s.send(OWNER, "Late answer")
    row = last(db)
    calls = []

    async def erase_while_deleting(method, **params):
        calls.append(method)
        db.run("DELETE FROM accounts WHERE id=?", (OWNER,))
        return True

    gateway.call = erase_while_deleting
    await deliver(db, gateway, c, row)
    assert calls == ["deleteMessage"]
    assert not db.one("SELECT * FROM account_meta WHERE user_id=?", (OWNER,))


@pytest.mark.parametrize("same_context", [True, False])
async def test_pending_progress_cannot_be_cancelled_from_another_chat(system, same_context):
    from conftest import CID, PEER

    db, s, _, c = system
    s.event = "pending-context"
    status = s.send(PEER, "Pending business status", CID, reply_menu=False)
    s.progress = status
    s.send(PEER if same_context else OWNER, "Result", CID if same_context else None)
    await deliver(db, Gateway(), c, last(db))
    state = db.one("SELECT state FROM outbox WHERE event_key=?", (status,))["state"]
    assert state == ("superseded" if same_context else "pending")


@pytest.mark.parametrize("allowed", [True, False])
async def test_business_file_cleanup_uses_business_api_and_respects_rights(system, allowed):
    from conftest import CID, PEER

    db, s, _, c = system
    conn = connection()
    conn["rights"]["can_delete_sent_messages"] = allowed
    db.put_connection(conn)
    gateway = Gateway()
    s.event = "business-file"
    status = s.send(PEER, "Preparing file", CID, reply_menu=False)
    await deliver(db, gateway, c, last(db))
    s.progress = status
    path = s.media.generated / "result.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Result")
    s.file(PEER, path, CID)

    async def upload(*args, **kwargs):
        return {"message_id": 88}

    gateway.upload = upload
    await deliver(db, gateway, c, last(db))
    deletes = [p for method, p in gateway.calls if method == "deleteBusinessMessages"]
    assert deletes == ([{"business_connection_id": CID, "message_ids": [77]}] if allowed else [])
    assert not any(m == "deleteMessage" for m, _ in gateway.calls)
