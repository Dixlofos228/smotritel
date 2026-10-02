# stories
import asyncio
import json
import secrets
import zipfile
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw
from .db import now, dump
from .ui import button


def frames(media, image, single=False):
    media.generated_capacity(25 * 1024 * 1024)
    media.assert_live()
    media.generated.mkdir(exist_ok=True, mode=0o700)
    paths = []
    nonce = secrets.token_hex(6)
    try:
        with Image.open(image) as src:
            square = ImageOps.fit(src.convert("RGB"), (1080, 1080))
            count = 1 if single else 9
            preview = Image.new("RGB", (720, 720), "#102032")
            for n in range(count):
                source = (
                    src.convert("RGB")
                    if single
                    else square.crop((n % 3 * 360, n // 3 * 360, (n % 3 + 1) * 360, (n // 3 + 1) * 360))
                )
                canvas = Image.new("RGB", (1080, 1920), "#102032")
                thumb = ImageOps.contain(source, (1080, 1920))
                canvas.paste(thumb, ((1080 - thumb.width) // 2, (1920 - thumb.height) // 2))
                path = media.generated / f"story-{nonce}-{n + 1}.jpg"
                media.assert_live()
                canvas.save(path, quality=90)
                path.chmod(0o600)
                paths.append(path)
                tile = ImageOps.fit(source, (240, 240))
                preview.paste(tile, (n % 3 * 240, n // 3 * 240))
                ImageDraw.Draw(preview).text(
                    (n % 3 * 240 + 8, n // 3 * 240 + 8),
                    str(n + 1),
                    fill="white",
                    stroke_width=2,
                    stroke_fill="black",
                )
            prev_path = media.generated / f"story-preview-{nonce}.jpg"
            media.assert_live()
            preview.save(prev_path)
            prev_path.chmod(0o600)
        media.assert_live()
        return paths, prev_path
    except BaseException:
        for path in media.generated.glob(f"story-*{nonce}*.jpg"):
            path.unlink(missing_ok=True)
        raise


async def prepare(s, image, cid, single=False):
    paths, preview = await asyncio.to_thread(frames, s.media, image, single)
    if not s.active():
        for path in [*paths, preview]:
            path.unlink(missing_ok=True)
        return
    if not cid:
        owned = s.db.all("SELECT id FROM connections WHERE owner_id=? AND enabled=1", (s.owner,))
        cid = owned[0]["id"] if len(owned) == 1 else None
    nonce = secrets.token_hex(6)
    draft = {
        "nonce": nonce,
        "cid": cid,
        "paths": [str(p) for p in paths],
        "expires": now() + 3600,
        "state": "preview",
        "published": [],
        "failed": [],
    }
    s.db.set_meta("story_draft", dump(draft))
    s.file(s.owner, preview, photo=True)
    bundle = s.media.generated / f"stories-{nonce}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for i, path in enumerate(paths):
            archive.write(path, arcname=f"Story {i + 1}.jpg")
    bundle.chmod(0o600)
    s.file(s.owner, bundle, filename="Stories 3×3.zip" if not single else "Story.zip")
    text = f"<b>🖼 Stories · {len(paths)} кадров готовы</b>\nРазмер каждого: 1080×1920.\n\nПредпросмотр и файлы отправлены вам. Публикация начнётся только после нажатия кнопки."
    rows = []
    conn = s.db.connection(cid) if cid else None
    if conn and conn["owner_id"] == s.owner and json.loads(conn["rights"]).get("can_manage_stories"):
        rows = [[button(f"Опубликовать {len(paths)} stories · 24 ч", f"publishstory:{nonce}")]]
    else:
        text += "\n\nДля публикации подключите Business-аккаунт и разрешите боту управление stories"
        rows = [[button("Подключение", "connect")]]
    rows += [s.ui.nav("fn:story")]
    s.ui.card(text, rows)


def draft_for(s, nonce):
    draft = json.loads(s.db.meta("story_draft", "{}") or "{}")
    if not s.active() or draft.get("nonce") != nonce or draft.get("expires", 0) < now():
        raise ValueError("Story draft expired")
    return draft


def publish(s, nonce):
    draft = draft_for(s, nonce)
    if draft["state"] != "preview":
        return
    conn = s.db.connection(draft.get("cid", ""))
    if (
        not conn
        or conn["owner_id"] != s.owner
        or not conn["enabled"]
        or not json.loads(conn["rights"]).get("can_manage_stories")
    ):
        raise ValueError("Story permission missing")
    paths = [Path(p).resolve() for p in draft["paths"]]
    if not 1 <= len(paths) <= 9:
        raise ValueError("Invalid story count")
    for path in paths:
        if (
            not path.is_relative_to(s.media.generated.resolve())
            or not path.is_file()
            or path.stat().st_size > 10 * 1024 * 1024
        ):
            raise ValueError("Invalid story file")
        with Image.open(path) as im:
            if im.size != (1080, 1920):
                raise ValueError("Invalid story dimensions")
    with s.db.transaction():
        draft["state"] = "publishing"
        draft["keys"] = []
        for i, path in enumerate(paths):
            s.part += 1
            key = f"{s.event}:{s.part}"
            s.db.enqueue(
                key,
                "postStory",
                {
                    "business_connection_id": conn["id"],
                    "content": {"type": "photo", "photo": "attach://photo"},
                    "active_period": 86400,
                    "_file": str(path),
                    "_field": "photo",
                    "_story_nonce": nonce,
                    "_story_index": i,
                },
            )
            draft["keys"].append(s.db.event_key(key))
        s.db.set_meta("story_draft", dump(draft))


def status(s, nonce, target=None):
    draft = draft_for(s, nonce)
    text = f"<b>🖼 Публикация stories</b>\nОпубликовано: {len(draft['published'])} из {len(draft['paths'])}.\nОшибок: {len(draft['failed'])}."
    if draft["state"] == "publishing":
        text += "\nTelegram обрабатывает очередь Нажмите «Обновить»"
    if draft["state"] == "done":
        text += "\nГотово"
    s.ui.card(text, [[button("↻ Обновить", f"storystatus:{nonce}")], s.ui.nav("fn:story")], target)


def receipt(tenant, nonce, index, success):
    draft = json.loads(tenant.meta("story_draft", "{}") or "{}")
    if draft.get("nonce") != nonce:
        return
    field = "published" if success else "failed"
    if index not in draft[field]:
        draft[field].append(index)
    if len(draft["published"]) + len(draft["failed"]) == len(draft["paths"]):
        draft["state"] = "done"
    tenant.set_meta("story_draft", dump(draft))
