import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from html.parser import HTMLParser
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smotritel.config import Config
from smotritel.db import DB, now
from smotritel.service import Service
from smotritel.media import Media


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean(text):
    p = Text()
    p.feed(text)
    return re.sub("[\U00010000-\U0010ffff\ufe0f]", "", "".join(p.parts)).strip()


async def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        db = DB(tmp)
        c = Config(Path(tmp), owner_id=777)
        db.put_connection(
            {
                "id": "preview",
                "user": {"id": 777},
                "user_chat_id": 777,
                "is_enabled": True,
                "rights": {"can_reply": True},
                "date": now(),
            }
        )
        s = Service(db, None, None, Media(db, None, c), c, account_id=777)
        cards = []
        for n, route in enumerate(("menu", "more", "launch:weather", "settings")):
            s.event = str(n)
            await s.ui.show(route)
            p = json.loads(db.one("SELECT payload FROM outbox ORDER BY id DESC")["payload"])
            assert "inline_keyboard" not in p["reply_markup"]
            cards.append(p)
        fontpath = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not Path(fontpath).exists():
            fontpath = "/usr/share/fonts/TTF/DejaVuSans.ttf"
        font = ImageFont.truetype(fontpath, 17)
        small = ImageFont.truetype(fontpath, 15)
        title = ImageFont.truetype(fontpath, 27)
        im = Image.new("RGB", (1260, 2270), (9, 17, 30))
        d = ImageDraw.Draw(im)
        d.text((30, 22), "Смотритель · управление в нижней панели", font=title, fill="#e3f0f7")
        d.text((30, 63), "Демонстрационные данные · не снимок клиента Telegram", font=font, fill="#90aabc")
        for n, p in enumerate(cards):
            x, y, w, h = 25 + n % 2 * 625, 105 + n // 2 * 1080, 585, 1050
            d.rounded_rectangle((x, y, x + w, y + h), 18, fill="#0e1723", outline="#294052", width=2)
            d.text(
                (x + 18, y + 18),
                ("Главная", "Ещё", "Ввод: погода", "Настройки")[n],
                font=title,
                fill="#55d7bd",
            )
            cy = y + 66
            if n == 0:
                banner = Image.open(root / "assets/banners/cover.jpg").resize((545, 272))
                im.paste(banner, (x + 20, cy))
                cy += 287
            textlines = []
            for line in clean(p["text"]).splitlines():
                cur = ""
                for word in line.split():
                    candidate = (cur + " " + word).strip()
                    if cur and d.textlength(candidate, font=font) > w - 40:
                        textlines.append(cur)
                        cur = word
                    else:
                        cur = candidate
                textlines.append(cur)
            for line in textlines[:13]:
                d.text((x + 20, cy), line, font=font, fill="#e1eaf1")
                cy += 23 if line else 12
            rows = p["reply_markup"]["keyboard"]
            ky = y + h - 20 - len(rows) * 34
            assert cy + 25 < ky, "Content overlaps bottom panel"
            d.line((x + 10, ky - 15, x + w - 10, ky - 15), fill="#355369", width=2)
            for row in rows:
                bw = (w - 40 - 8 * (len(row) - 1)) // len(row)
                for k, label in enumerate(row):
                    bx = x + 20 + k * (bw + 8)
                    d.rounded_rectangle((bx, ky, bx + bw, ky + 28), 6, fill="#203444")
                    label = clean(label)
                    while d.textlength(label, font=small) > bw - 16:
                        label = label[:-2] + "…" if len(label) > 2 else ""
                    d.text((bx + 8, ky + 5), label, font=small, fill="#e7eef5")
                ky += 34
        target = root / "docs/LOWER_PREVIEW.png"
        im.save(target)
        db.close()
        print("превью готово, тестовые данные")


if __name__ == "__main__":
    asyncio.run(main())
