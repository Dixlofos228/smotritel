import io
import json
import hashlib
import zipfile
from pathlib import Path
import pytest
from PIL import Image
from conftest import OWNER, PEER, CID, business, direct, connection, out_text
from test_delivery import inline
from smotritel.features import SPECS, CATEGORIES
from smotritel.service import Service
from smotritel.media import Media
from smotritel.delivery import deliver, business_allowed, record_failure
from smotritel.jobs import run_one
from smotritel.db import now


def latest(db):
    return json.loads(db.one("SELECT payload FROM outbox ORDER BY id DESC")["payload"])


@pytest.mark.parametrize("key", SPECS)
async def test_feature_card_has_working_entry_and_return_navigation(system, key):
    db, s, *_ = system
    await s.handle(inline("fn:" + key, 55, 100))
    p = latest(db)
    assert SPECS[key][1] in p["text"] and SPECS[key][3] in p["text"]
    from test_ui import choices

    buttons = choices(s, p)
    assert len(buttons) >= 3 and any(b["callback_data"] == "menu" for b in buttons)
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


@pytest.mark.parametrize("category", CATEGORIES)
async def test_catalogue_category_lists_actions_without_a_command_wall(system, category):
    db, s, *_ = system
    await s.handle(inline("cat:" + category, 55, 100))
    p = latest(db)
    assert len(p["text"]) < 500
    callbacks = set(json.loads(s.db.meta("lower_routes")).values())
    assert all(f"fn:{key}" in callbacks for key, spec in SPECS.items() if spec[0] == category)
    assert all(len(r) <= 2 for r in p["reply_markup"]["keyboard"])


async def foreign_data(db):
    db.put_connection(connection(owner=777, cid="foreign"))
    ev = business(20, "FOREIGN PRIVATE", 20)
    ev["business_message"]["business_connection_id"] = "foreign"
    db.archive(ev["business_message"], 20)
    return db.one("SELECT rowid rid FROM chats WHERE connection_id='foreign'")["rid"], db.one(
        "SELECT rowid rid FROM messages WHERE connection_id='foreign'"
    )["rid"]


@pytest.mark.parametrize(
    "route",
    [
        "run:stat:{chat}",
        "setchat:echo:{chat}:on",
        "perform:save:{message}",
        "perform:q:{message}",
        "perform:story:{message}",
    ],
)
async def test_guessed_foreign_objects_do_not_run_or_leak(system, route):
    db, s, *_ = system
    chat, msg = await foreign_data(db)
    await s.handle(inline(route.format(chat=chat, message=msg), 55, 100))
    assert "FOREIGN PRIVATE" not in out_text(db)
    assert not db.all("SELECT * FROM saved") and not db.settings("foreign", PEER)
    assert not db.all("SELECT * FROM jobs")


async def test_select_chat_toggle_is_explicit_and_idempotent(system):
    db, s, *_ = system
    await s.handle(business(1, "hello"))
    rid = db.one("SELECT rowid rid FROM chats WHERE connection_id=?", (CID,))["rid"]
    await s.handle(inline(f"run:echo:{rid}", 55, 100))
    assert not db.settings(CID, PEER).get("echo")
    await s.handle(inline(f"setchat:echo:{rid}:on", 55, 101))
    await s.handle(inline(f"setchat:echo:{rid}:on", 55, 102))
    assert db.settings(CID, PEER)["echo"] is True
    assert all(
        json.loads(r["payload"]).get("chat_id") == OWNER
        for r in db.all("SELECT payload FROM outbox WHERE method!='answerCallbackQuery'")
    )


async def test_feature_input_cancellation_does_not_apply_to_any_chat(system):
    db, s, *_ = system
    await s.handle(business(1, "hello"))
    rid = db.one("SELECT rowid rid FROM chats")["rid"]
    await s.handle(inline(f"run:afk:{rid}", 55, 100))
    assert s.db.meta("input_mode") == "feature"
    await s.handle(direct(text="🏠 Главная", update_id=101))
    await s.handle(direct(text="should not become AFK", update_id=102))
    assert not db.settings(CID, PEER).get("afk")
    assert not s.db.meta("input_mode") and not s.db.meta("feature_context")


async def test_selected_afk_input_updates_only_that_chat(system):
    db, s, *_ = system
    await s.handle(business(1, "hello"))
    rid = db.one("SELECT rowid rid FROM chats")["rid"]
    await s.handle(inline(f"run:afk:{rid}", 55, 100))
    await s.handle(direct(text="Вернусь позже", update_id=101))
    assert db.settings(CID, PEER)["afk"] == "Вернусь позже"
    assert not s.db.meta("setting:afk") and not s.db.meta("input_mode")
    assert latest(db)["chat_id"] == OWNER and "business_connection_id" not in latest(db)


async def test_quote_from_business_archive_is_queued_and_delivered_privately(system):
    db, s, _, c = system
    await s.handle(business(1, "Quote text", 7))
    rid = db.one("SELECT rowid rid FROM messages")["rid"]
    root = Service(db, s.tg, s.ai, Media(db, s.tg, c), c)
    await root.handle(inline(f"perform:q:{rid}", 55, 100))
    job = db.one("SELECT * FROM jobs")
    assert json.loads(job["payload"])["private"]
    status = db.one(
        "SELECT payload FROM outbox WHERE json_extract(payload,'$._replace_event') IS NULL AND event_key LIKE '%:100:%' AND method='sendMessage' ORDER BY id DESC"
    )
    assert json.loads(status["payload"])["chat_id"] == OWNER
    await run_one(root)
    files = db.all("SELECT payload FROM outbox WHERE method='sendPhoto'")
    assert len(files) == 1
    p = json.loads(files[0]["payload"])
    assert p["chat_id"] == OWNER and "business_connection_id" not in p
    assert Path(p["_file"]).is_relative_to(c.data_dir / "tenants" / str(OWNER))
    with Image.open(p["_file"]) as im:
        assert im.width == 900


async def test_plain_tiktok_link_routes_to_a_job_and_cancel_does_not_silently_ignore(system, monkeypatch):
    db, s, _, c = system
    c.tiktok = True

    async def download(url, root, limit, guard=None):
        root.mkdir(exist_ok=True)
        p = root / "public.mp4"
        p.write_bytes(b"fixture video")
        return [p]

    monkeypatch.setattr("smotritel.tiktok.download_all", download)
    root = Service(db, s.tg, s.ai, Media(db, s.tg, c), c)
    await root.handle(direct(text="https://www.tiktok.com/@test/video/123", update_id=100))
    assert db.one("SELECT id FROM jobs")
    await run_one(root)
    assert db.one("SELECT state FROM jobs")["state"] == "done"
    assert db.one("SELECT method FROM outbox WHERE method='sendDocument'")


async def test_repeat_command_uses_owner_mirror_even_from_business_chat(system):
    db, s, *_ = system
    s.c.test_chat = 0
    await s.handle(business(100, ".spam 3 MIRROR", 100, owner=True))
    copies = [
        json.loads(r["payload"]) for r in db.all("SELECT payload FROM outbox WHERE method='sendMessage'")
    ]
    assert len(copies) == 3 and all(
        p["chat_id"] == OWNER and "business_connection_id" not in p for p in copies
    )
    assert [r["next_attempt"] for r in db.all("SELECT * FROM outbox ORDER BY id")][2] >= now() + 3
    await s.handle(business(101, ".spam 3 AGAIN", 101, owner=True))
    assert "AGAIN" not in out_text(db)


async def test_antimute_is_owner_mirror_and_never_a_reply_loop(system):
    db, s, *_ = system
    await s.handle(business(100, ".antimute", 100, owner=True))
    assert db.settings(CID, PEER)["antimute"]
    await s.handle(business(101, "outgoing", 101, owner=True))
    assert "Зеркало исходящего" in latest(db)["text"] and latest(db)["chat_id"] == OWNER
    count = db.one("SELECT count(*) n FROM outbox")["n"]
    await s.handle(business(102, "bot outgoing", 102, owner=True, sender_business_bot={"id": 12}))
    assert db.one("SELECT count(*) n FROM outbox")["n"] == count


async def test_autoreply_without_argument_toggles_and_mute_pauses(system):
    db, s, *_ = system
    await s.handle(business(100, ".autoreply", 100, owner=True))
    assert db.settings(CID, PEER)["autoreply"]
    await s.handle(business(101, ".mute 5m", 101, owner=True))
    before = db.one("SELECT count(*) n FROM outbox")["n"]
    await s.handle(business(102, "no answer", 102))
    assert db.one("SELECT count(*) n FROM outbox")["n"] == before
    await s.handle(business(103, ".unmute", 103, owner=True))
    assert db.settings(CID, PEER)["muted_until"] == 0
    await s.handle(business(104, ".autoreply", 104, owner=True))
    assert not db.settings(CID, PEER)["autoreply"]


async def test_api_rotate_sends_private_file_and_repeated_callback_keeps_that_key(system):
    db, s, *_ = system
    event = inline("apikey:rotate", 55, 100)
    await s.handle(event)
    digest = s.db.meta("api_hash")
    file = json.loads(db.one("SELECT payload FROM outbox WHERE method='sendDocument'")["payload"])
    path = Path(file["_file"])
    key = path.read_text().splitlines()[0]
    assert hashlib.sha256(key.encode()).hexdigest() == digest
    assert path.stat().st_mode & 0o777 == 0o600
    assert key not in out_text(db)
    await s.handle(event)
    assert s.db.meta("api_hash") == digest
    await s.handle(inline("apikey:off", 55, 101))
    assert not s.db.meta("api_hash")


async def test_premium_is_demo_and_persists_without_invoices(system):
    db, s, *_ = system
    await s.handle(inline("plan:demo", 55, 100))
    assert s.db.meta("plan") == "demo-premium" and "Никаких списаний" in latest(db)["text"]
    assert not db.all("SELECT * FROM outbox WHERE method='sendInvoice'")


async def test_yandex_returns_information_in_chat_without_inline_links(system, monkeypatch):
    from smotritel import web_search

    db, s, *_ = system
    s.ai.session = None
    queries = []

    async def found(session, ai, query):
        queries.append(query)
        return "Информация из источника: " + query + "\nhttps://example.org/"

    monkeypatch.setattr(web_search, "answer", found)
    await s.handle(inline("begin:yandex", 55, 100))
    await s.handle(direct(text="<b>x&y</b>", update_id=101))
    p = latest(db)
    assert queries == ["<b>x&y</b>"]
    assert "<b>x&y</b>" in p["text"] and not p.get("parse_mode")
    assert "inline_keyboard" not in p.get("reply_markup", {})


async def test_actual_heart_file_is_animated_and_demo_check_is_visible(system):
    db, s, *_ = system
    await s.handle(direct(text="/heart", update_id=100))
    p = json.loads(db.one("SELECT payload FROM outbox WHERE method='sendAnimation'")["payload"])
    with Image.open(p["_file"]) as im:
        assert im.n_frames > 1 and im.format == "GIF"
    await s.handle(direct(text="/send USDT 0.03", update_id=101))
    with Image.open(
        latest(db).get("_file")
        or json.loads(db.one("SELECT payload FROM outbox WHERE method='sendPhoto'")["payload"])["_file"]
    ) as im:
        assert im.size == (900, 520)
    assert "DEMO" in out_text(db) and "НЕ выполнен" in out_text(db)


async def test_story_preview_requires_confirm_then_uploads_nine_right_scoped_frames(system, tmp_path):
    from smotritel.stories import prepare, publish

    db, s, tg, c = system
    conn = connection()
    conn["rights"] = {"can_manage_stories": True}
    db.put_connection(conn)
    image = tmp_path / "source.jpg"
    Image.new("RGB", (600, 500), "red").save(image)
    s.event = "story"
    await prepare(s, image, CID)
    assert not db.all("SELECT id FROM outbox WHERE method='postStory'")
    draft = json.loads(s.db.meta("story_draft"))
    bundle = json.loads(db.one("SELECT payload FROM outbox WHERE method='sendDocument'")["payload"])
    with zipfile.ZipFile(bundle["_file"]) as archive:
        assert len(archive.namelist()) == 9
    for p in draft["paths"]:
        with Image.open(p) as im:
            assert im.size == (1080, 1920)
    publish(s, draft["nonce"])
    publish(s, draft["nonce"])
    rows = db.all("SELECT * FROM outbox WHERE method='postStory'")
    assert len(rows) == 9 and business_allowed(db, OWNER, CID, "postStory")

    class StoryGateway:
        async def upload(self, method, payload, path, field):
            assert (
                method == "postStory" and payload["content"]["photo"] == "attach://photo" and field == "photo"
            )
            assert payload["active_period"] == 86400 and not any(k.startswith("_") for k in payload)
            return {"id": 42, "chat": {"id": OWNER}}

    for row in rows:
        await deliver(db, StoryGateway(), c, row)
    done = json.loads(s.db.meta("story_draft"))
    assert done["state"] == "done" and len(done["published"]) == 9


async def test_story_nonce_file_and_revoked_rights_cannot_cross_accounts(system, tmp_path):
    from smotritel.stories import prepare, publish

    db, s, *_ = system
    image = tmp_path / "source.jpg"
    Image.new("RGB", (60, 50), "red").save(image)
    s.event = "story"
    await prepare(s, image, CID, True)
    draft = json.loads(s.db.meta("story_draft"))
    with pytest.raises(ValueError):
        publish(s, draft["nonce"])
    assert not db.all("SELECT * FROM outbox WHERE method='postStory'")
    db.put_connection(connection(owner=777, cid="foreign"))
    foreign = Service(db, s.tg, s.ai, s.media, s.c, account_id=777)
    with pytest.raises(ValueError):
        publish(foreign, draft["nonce"])


async def test_story_terminal_failure_updates_own_progress(system, tmp_path):
    from smotritel.stories import prepare, publish

    db, s, *_ = system
    conn = connection()
    conn["rights"]["can_manage_stories"] = True
    db.put_connection(conn)
    p = tmp_path / "source.jpg"
    Image.new("RGB", (60, 60), "red").save(p)
    s.event = "story"
    await prepare(s, p, CID, True)
    publish(s, json.loads(s.db.meta("story_draft"))["nonce"])
    row = db.one("SELECT * FROM outbox WHERE method='postStory'")
    record_failure(db, row)
    draft = json.loads(s.db.meta("story_draft"))
    assert draft["failed"] == [0] and draft["state"] == "done"


async def test_name_clock_rights_persistence_minute_limit_and_restore(system, monkeypatch):
    from smotritel.clocks import toggle, tick

    db, s, *_ = system
    conn = connection()
    conn["rights"] = {"can_change_name": True}
    db.put_connection(conn)
    s.update_id = 100
    await toggle(s, "timename", db.connection(CID), "on")
    assert json.loads(s.db.meta("clock:timename"))["enabled"]
    tick(db)
    rows = db.all("SELECT * FROM outbox WHERE method='setBusinessAccountName'")
    assert len(rows) == 1 and business_allowed(db, OWNER, CID, rows[0]["method"])
    assert " · " in json.loads(rows[0]["payload"])["first_name"]
    s.update_id = 101
    await toggle(s, "timename", db.connection(CID), "off")
    assert not json.loads(s.db.meta("clock:timename"))["enabled"]
    p = json.loads(
        db.one("SELECT payload FROM outbox WHERE method='setBusinessAccountName' ORDER BY id DESC")["payload"]
    )
    assert p["first_name"] == "Owner"


async def test_bio_clock_gets_original_text_and_restores_it(system):
    from smotritel.clocks import toggle

    db, s, tg, *_ = system
    conn = connection()
    conn["rights"] = {"can_change_bio": True}
    db.put_connection(conn)

    async def get(method, **params):
        return {"id": OWNER, "type": "private", "bio": "Original bio"}

    tg.call = get
    s.update_id = 100
    await toggle(s, "timebio", db.connection(CID), "on")
    s.update_id = 101
    await toggle(s, "timebio", db.connection(CID), "off")
    p = json.loads(
        db.one("SELECT payload FROM outbox WHERE method='setBusinessAccountBio' ORDER BY id DESC")["payload"]
    )
    assert p["bio"] == "Original bio"


async def test_bio_watch_sampler_respects_watch_scope_and_notifies_privately(system):
    from smotritel.watch_sampling import sample

    db, s, tg, *_ = system
    await s.handle(business(1, "hello"))
    db.run("INSERT INTO watches VALUES(?,?)", (CID, PEER))

    async def get(method, **params):
        return {"id": PEER, "type": "private", "first_name": "Peer", "username": "peer", "bio": "Visible bio"}

    tg.call = get
    await sample(s)
    assert "Visible bio" in out_text(db) and "Изменение наблюдаемого профиля" in out_text(db)
    count = db.one("SELECT count(*) n FROM outbox")["n"]
    await sample(s)
    assert db.one("SELECT count(*) n FROM outbox")["n"] == count


def test_tiktok_photo_hydration_and_cdn_allowlist():
    from smotritel.tiktok import image_urls, validate_cdn, DownloadUnavailable

    data = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "id": "123",
                        "imagePost": {
                            "images": [{"imageURL": {"urlList": ["https://p16.tiktokcdn.com/a.jpg"]}}]
                        },
                    }
                }
            }
        }
    }
    page = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">' + json.dumps(data) + "</script>"
    assert image_urls(page, "123") == ["https://p16.tiktokcdn.com/a.jpg"]
    with pytest.raises(DownloadUnavailable):
        image_urls(page, "999")
    for url in ("https://127.0.0.1/a", "https://evil.test/a", "https://tiktokcdn.com.evil.test/a"):
        with pytest.raises(DownloadUnavailable):
            validate_cdn(url)


async def test_english_keyboard_is_installed_and_its_labels_still_route(system):
    from smotritel.navigation import MAIN_EN

    db, s, *_ = system
    await s.handle(direct(text="/lang en", update_id=100))
    assert s.db.meta("lang") == "en"
    assert any(
        json.loads(r["payload"]).get("reply_markup", {}).get("keyboard") == MAIN_EN
        for r in db.all("SELECT payload FROM outbox")
    )
    await s.handle(direct(text="🗂Archive", update_id=101))
    assert "Архив" in latest(db)["text"] and latest(db)["reply_markup"]["keyboard"]


def test_profile_ticks_coalesce_when_delivery_is_delayed(system, monkeypatch):
    from smotritel.clocks import tick

    db, s, *_ = system
    conn = connection()
    conn["rights"]["can_change_name"] = True
    db.put_connection(conn)
    s.db.set_meta(
        "clock:timename",
        json.dumps({"cid": CID, "enabled": True, "last_tick": 0, "first_name": "Original", "last_name": ""}),
    )
    tick(db)
    clock_now = now()
    monkeypatch.setattr("smotritel.clocks.now", lambda: clock_now + 60)
    tick(db)
    assert (
        db.one("SELECT count(*) n FROM outbox WHERE method='setBusinessAccountName' AND state='pending'")["n"]
        == 1
    )
    assert db.one("SELECT count(*) n FROM outbox WHERE state='superseded'")["n"] == 1
    conn["rights"] = {}
    db.put_connection(conn)
    monkeypatch.setattr("smotritel.clocks.now", lambda: clock_now + 120)
    tick(db)
    assert not json.loads(s.db.meta("clock:timename"))["enabled"]


def test_generated_content_respects_account_budget(system):
    _, s, *_ = system
    s.c.tenant_media_budget = 100
    with pytest.raises(ValueError, match="budget"):
        s.media.heart()


async def test_no_reply_permission_prevents_enabling_chat_automation(system):
    db, s, *_ = system
    await s.handle(business(1, "hello"))
    conn = connection()
    conn["rights"] = {}
    db.put_connection(conn)
    rid = db.one("SELECT rowid rid FROM chats")["rid"]
    await s.handle(inline(f"setchat:echo:{rid}:on", 55, 100))
    assert not db.settings(CID, PEER).get("echo")


async def test_slideshow_download_is_bounded_and_writes_owned_jpegs(system, monkeypatch):
    from smotritel.tiktok import photos

    _, s, *_ = system
    image = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(image, format="PNG")
    doc = {
        "ItemModule": {
            "123": {
                "id": "123",
                "imagePost": {
                    "images": [
                        {"imageURL": {"urlList": ["https://p.tiktokcdn.com/1"]}},
                        {"imageURL": {"urlList": ["https://p.tiktokcdn.com/2"]}},
                    ]
                },
            }
        }
    }
    html = '<script id="SIGI_STATE">' + json.dumps(doc) + "</script>"

    async def get(session, url, limit, source=False):
        return (
            ("https://www.tiktok.com/@test/photo/123", html.encode()) if source else (url, image.getvalue())
        )

    monkeypatch.setattr("smotritel.tiktok.bounded_get", get)
    paths = await photos(
        "https://www.tiktok.com/@test/photo/123", s.media.generated, 1024 * 1024, s.media.assert_live
    )
    assert len(paths) == 2 and all(p.is_relative_to(s.media.generated) for p in paths)
    for p in paths:
        with Image.open(p) as im:
            assert im.format == "JPEG"


def test_story_generation_cancel_cleans_partial_files_only(system, tmp_path, monkeypatch):
    from smotritel.stories import frames

    _, s, *_ = system
    image = tmp_path / "source.jpg"
    Image.new("RGB", (60, 50), "red").save(image)
    s.media.generated.mkdir(exist_ok=True)
    other = s.media.generated / "story-other.jpg"
    other.write_bytes(b"keep")
    calls = 0

    def revoked():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise ValueError("Account removed")

    monkeypatch.setattr(s.media, "assert_live", revoked)
    with pytest.raises(ValueError, match="Account removed"):
        frames(s.media, image)
    assert list(s.media.generated.iterdir()) == [other]
    assert other.read_bytes() == b"keep"


def test_tiktok_worker_refuses_library_challenge_solver(monkeypatch):
    from yt_dlp.extractor.tiktok import TikTokBaseIE
    from yt_dlp.utils import ExtractorError
    from smotritel.tiktok import configure_public_only

    monkeypatch.setattr(TikTokBaseIE, "_solve_challenge_and_set_cookies", lambda *_: "solved")
    configure_public_only()
    with pytest.raises(ExtractorError, match="download stopped"):
        TikTokBaseIE._solve_challenge_and_set_cookies(None, "challenge page")
