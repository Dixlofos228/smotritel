from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math

ROOT = Path(__file__).resolve().parents[1] / "assets"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
if not Path(FONT).exists():
    FONT = "/usr/share/fonts/TTF/DejaVuSans.ttf"


def font(n):
    return ImageFont.truetype(FONT, n)


def card(title, subtitle, phase=0):
    im = Image.new("RGB", (1200, 600))
    d = ImageDraw.Draw(im)
    for y in range(600):
        t = y / 600
        d.line((0, y, 1200, y), fill=(int(9 + 8 * t), int(17 + 12 * t), int(32 + 24 * t)))
    for x in range(0, 1200, 60):
        d.line((x, 0, x, 600), fill=(22, 36, 54))
    for y in range(0, 600, 60):
        d.line((0, y, 1200, y), fill=(22, 36, 54))
    cx, cy = 940, 295
    for r in (90, 150, 215):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(33, 65, 79), width=2)
    a = phase * 2 * math.pi
    d.line((cx, cy, cx + 210 * math.cos(a), cy + 210 * math.sin(a)), fill=(52, 211, 181), width=3)

    d.rounded_rectangle((855, 215, 1025, 375), 32, fill=(16, 35, 48), outline=(76, 227, 197), width=4)
    d.arc((878, 240, 1002, 350), 180, 360, fill=(235, 245, 245), width=5)
    d.arc((878, 240, 1002, 350), 0, 180, fill=(235, 245, 245), width=5)
    d.ellipse((918, 273, 962, 317), fill=(76, 227, 197))
    d.ellipse((933, 285, 947, 299), fill=(13, 28, 43))
    d.text((74, 76), "СМОТРИТЕЛЬ  /  LOCAL EDITION", font=font(20), fill=(90, 207, 187))
    d.text((70, 205), title, font=font(64), fill=(242, 246, 251))
    d.text((74, 308), subtitle, font=font(24), fill=(163, 183, 203))
    d.line((74, 465, 650, 465), fill=(45, 77, 94), width=2)
    d.text((74, 487), "История остаётся с вами", font=font(23), fill=(165, 188, 207))
    return im


for folder, title, subtitle in [
    ("banners", "Смотритель", "Ваш локальный архив Telegram"),
    ("menu", "Ваше пространство", "Архив · история · локальный AI"),
    ("status", "Подключение", "Состояние системы и Business"),
    ("help", "Все функции", "Понятные инструменты для ваших чатов"),
    ("archive", "История рядом", "Сообщения · версии · сохранённые медиа"),
    ("ai", "Локальный AI", "Вопросы, расшифровки и краткие итоги"),
]:
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
    card(title, subtitle).save(ROOT / folder / "cover.jpg", quality=88)
frames = [card("Смотритель", "Ваш локальный архив Telegram", i / 18).resize((600, 300)) for i in range(18)]
frames[0].save(
    ROOT / "banners" / "welcome.gif",
    save_all=True,
    append_images=frames[1:],
    duration=100,
    loop=0,
    optimize=True,
)
logo = card("", "").crop((825, 180, 1055, 410)).resize((512, 512))
logo.save(ROOT / "logo" / "mark.png")
logo.save(ROOT / "logo" / "mark.jpg", quality=94)
(ROOT / "logo" / "mark.svg").write_text(
    """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256" rx="56" fill="#0c1b2b"/><rect x="40" y="44" width="176" height="168" rx="38" fill="#102330" stroke="#4ce3c5" stroke-width="4"/><path d="M62 128Q128 46 194 128Q128 210 62 128Z" fill="none" stroke="#edf6f6" stroke-width="6"/><circle cx="128" cy="128" r="26" fill="#4ce3c5"/><circle cx="128" cy="128" r="9" fill="#0c1b2b"/></svg>"""
)
print("картинки готовы")
