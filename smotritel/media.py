import asyncio
import hashlib
import os
import shutil
from pathlib import Path
from .db import now
from .telegram import TelegramError


class Media:
    def __init__(self, db, telegram, config, tenant_id=None):
        self.db, self.tg, self.c = db, telegram, config
        self.lock = asyncio.Lock()
        self.uid = tenant_id
        self.gen = (
            (db.one("SELECT generation FROM accounts WHERE id=?", (tenant_id,)) or {}).get("generation")
            if tenant_id
            else None
        )
        self.directory = config.data_dir / "tenants" / str(tenant_id) if tenant_id else config.data_dir
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if tenant_id and self.gen:
            (self.directory / ".generation").write_text(self.gen)
        self.generated = self.directory / "generated"
        self.root = self.directory / "media"
        self.root.mkdir(exist_ok=True, mode=0o700)

    def for_account(self, uid):
        bound = Media(self.db, self.tg, self.c, tenant_id=uid)
        bound.lock = self.lock
        return bound

    def authorized(self, row):
        if self.uid is None:
            return True
        from .tenancy import owner_of

        version = self.db.one("SELECT connection_id,chat_id FROM versions WHERE id=?", (row["version_id"],))
        return version and owner_of(self.db, version["connection_id"], version["chat_id"]) == self.uid

    def assert_live(self):
        if self.uid and (
            not (self.directory / ".generation").is_file()
            or (self.directory / ".generation").read_text() != self.gen
        ):
            raise ValueError("Account removed")

    def generated_capacity(self, expected):
        self.assert_live()
        used = sum(p.stat().st_size for p in self.directory.rglob("*") if p.is_file())
        if (
            used + expected > min(self.c.tenant_media_budget, self.c.budget)
            or shutil.disk_usage(self.directory).free < expected + 64 * 1024 * 1024
        ):
            raise ValueError("Generated file budget exceeded")

    async def fetch(self, row):
        if not self.authorized(row):
            raise ValueError("Media belongs to another account")

        async with self.lock:
            cur = self.db.one("SELECT * FROM media WHERE id=?", (row["id"],))
            if cur and cur["state"] == "pending":
                self.assert_live()
                await self._fetch(cur)

    async def _fetch(self, row):
        tmp = self.root / f".part-{row['id']}"
        state = None
        try:
            if (row.get("size") or 0) > self.c.max_media:
                state = "too_large"
                return
            used = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
            if (
                used + self.c.max_media > min(self.c.budget, self.c.tenant_media_budget)
                or self.db.one(
                    "SELECT coalesce(sum(size),0) n FROM (SELECT max(size) size FROM media WHERE state='ready' GROUP BY path)"
                )["n"]
                + self.c.max_media
                > self.c.budget
                or shutil.disk_usage(self.root).free < self.c.max_media + 64 * 1024 * 1024
            ):
                state = "budget_exceeded"
                return
            meta = await self.tg.download(row["file_id"], tmp, self.c.max_media)
            with tmp.open("rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            ext = Path(meta.get("file_path", "")).suffix.lower()
            if not ext or len(ext) > 8 or not ext[1:].isalnum():
                ext = ".bin"
            final = self.root / (sha + ext)
            self.assert_live()
            os.replace(tmp, final)
            final.chmod(0o600)
            self.db.run(
                "UPDATE media SET path=?,sha256=?,size=?,state='ready' WHERE id=?",
                (str(final.relative_to(self.c.data_dir)), sha, final.stat().st_size, row["id"]),
            )
        except TelegramError as e:
            if e.code == 413:
                state = "too_large"
            else:
                attempts = row["attempts"] + 1
                state = "failed" if attempts >= 8 else "pending"
                self.db.run(
                    "UPDATE media SET attempts=?,next_attempt=? WHERE id=?",
                    (attempts, now() + max(e.retry_after, min(3600, 2**attempts * 5)), row["id"]),
                )
        finally:
            tmp.unlink(missing_ok=True)
            if state:
                self.db.run("UPDATE media SET state=? WHERE id=?", (state, row["id"]))

    async def ensure(self, version_ids):
        if not version_ids:
            return []
        slots = ",".join("?" for _ in version_ids)
        for row in self.db.all(
            f"SELECT * FROM media WHERE version_id IN ({slots}) AND state='pending'", tuple(version_ids)
        ):
            await self.fetch(row)
        return self.db.all(f"SELECT * FROM media WHERE version_id IN ({slots})", tuple(version_ids))

    def path(self, row):
        path = (self.c.data_dir / row["path"]).resolve()
        if (
            not self.authorized(row)
            or not path.is_relative_to(self.directory.resolve())
            or not path.is_file()
        ):
            raise ValueError("Media path unavailable")
        return path

    def render_quote(self, lines, image=None):
        self.generated_capacity(8 * 1024 * 1024)
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        import textwrap

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not Path(font_path).exists():
            font_path = "/usr/share/fonts/TTF/DejaVuSans.ttf"
        font = (
            ImageFont.truetype(font_path, 30) if Path(font_path).exists() else ImageFont.load_default(size=30)
        )
        wrapped = []
        for line in lines:
            wrapped.extend(textwrap.wrap(line, 46) or [""])
        wrapped = wrapped[:40]
        canvas = Image.new("RGB", (900, max(400, 170 + len(wrapped) * 43) + (450 if image else 0)), "#102032")
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((30, 30, 870, canvas.height - 30), radius=30, fill="#193448")
        draw.text((70, 65), "СМОТРИТЕЛЬ  /  ЦИТАТА", font=font, fill="#64d8b1")
        y = 130
        if image:
            with Image.open(image) as photo:
                thumb = ImageOps.contain(photo.convert("RGB"), (760, 400))
                canvas.paste(thumb, (70, y))
            y += 450
        for line in wrapped:
            draw.text((70, y), line, font=font, fill="white")
            y += 43
        self.assert_live()
        target = self.generated
        target.mkdir(exist_ok=True, mode=0o700)
        path = target / f"quote-{now()}-{os.urandom(4).hex()}.png"
        canvas.save(path)
        return path

    def story(self, image, grid=False):
        from PIL import Image, ImageOps

        self.assert_live()
        target = self.generated
        target.mkdir(exist_ok=True, mode=0o700)
        paths = []
        with Image.open(image) as src:
            if grid:
                canvas = ImageOps.fit(src.convert("RGB"), (1080, 1080))
                for i in range(9):
                    tile = canvas.crop((i % 3 * 360, i // 3 * 360, i % 3 * 360 + 360, i // 3 * 360 + 360))
                    p = target / f"grid-{now()}-{os.urandom(3).hex()}-{i + 1}.jpg"
                    tile.save(p)
                    paths.append(p)
            else:
                canvas = Image.new("RGB", (1080, 1920), "#102032")
                thumb = ImageOps.contain(src.convert("RGB"), (1080, 1920))
                canvas.paste(thumb, ((1080 - thumb.width) // 2, (1920 - thumb.height) // 2))
                p = target / f"story-{now()}-{os.urandom(3).hex()}.jpg"
                canvas.save(p)
                paths.append(p)
        return paths

    def heart(self):
        self.generated_capacity(1024 * 1024)
        import math
        from PIL import Image, ImageDraw

        self.assert_live()
        self.generated.mkdir(exist_ok=True, mode=0o700)
        frames = []
        for i in range(16):
            im = Image.new("RGB", (480, 480), "#102032")
            draw = ImageDraw.Draw(im)
            scale = 10 + 1.5 * math.sin(i * math.pi / 8)
            points = []
            for n in range(180):
                t = n * math.pi / 90
                points.append(
                    (
                        240 + scale * 16 * math.sin(t) ** 3,
                        250
                        - scale
                        * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)),
                    )
                )
            draw.polygon(points, fill="#fa5b85")
            frames.append(im)
        self.assert_live()
        path = self.generated / ("heart-" + os.urandom(6).hex() + ".gif")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=90, loop=0)
        path.chmod(0o600)
        return path

    def demo_check(self, arg):
        self.generated_capacity(2 * 1024 * 1024)
        from decimal import Decimal, InvalidOperation
        from PIL import Image, ImageDraw, ImageFont

        currency, amount = arg.strip().split()
        if currency.upper() not in ("USDT", "TON", "USD", "EUR", "RUB"):
            raise ValueError("Unknown demo currency")
        try:
            value = Decimal(amount)
        except InvalidOperation:
            raise ValueError("Invalid demo amount") from None
        if not value.is_finite() or not Decimal("0") < value <= Decimal("1000000000"):
            raise ValueError("Invalid demo amount")
        self.assert_live()
        self.generated.mkdir(exist_ok=True, mode=0o700)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

        def font(size):
            return (
                ImageFont.truetype(font_path, size)
                if Path(font_path).exists()
                else ImageFont.load_default(size=size)
            )

        im = Image.new("RGB", (900, 520), "#102032")
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((25, 25, 875, 495), radius=32, fill="#193448")
        d.text((65, 60), "СМОТРИТЕЛЬ / DEMO", font=font(33), fill="#64d8b1")
        d.text((65, 150), f"{value.normalize():f} {currency.upper()}", font=font(58), fill="white")
        d.text((65, 270), "СРЕДСТВА НЕ ОТПРАВЛЕНЫ", font=font(32), fill="#fa8e85")
        d.text((65, 345), "Образец чека • без кошелька", font=font(29), fill="#bac9d3")
        d.text((65, 398), "Без платежа и транзакции", font=font(29), fill="#bac9d3")
        self.assert_live()
        path = self.generated / ("demo-" + os.urandom(6).hex() + ".png")
        im.save(path)
        path.chmod(0o600)
        return path
