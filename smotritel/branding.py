# оформление бота
from pathlib import Path


async def apply(tg, db):
    if db.meta("branding_version") == "2":
        return
    await tg.call("setMyName", name="Смотритель")
    await tg.call(
        "setMyDescription",
        description="История остаётся с вами\n\nСмотритель сохраняет полученные Telegram Business сообщения, правки и доступные медиа на сервере Смотрителя У каждого пользователя свой архив AI работает на сервере без внешнего AI по умолчанию\n\nНажмите Start, затем подключите бота в настройках Telegram Business",
    )
    await tg.call(
        "setMyShortDescription",
        short_description="Локальный архив Telegram Business · сообщения, версии, медиа и AI История остаётся с вами",
    )
    await tg.call("setChatMenuButton", menu_button={"type": "commands"})
    path = Path(__file__).resolve().parents[1] / "assets/logo/mark.jpg"
    await tg.upload(
        "setMyProfilePhoto", {"photo": {"type": "static", "photo": "attach://portrait"}}, path, "portrait"
    )
    db.set_meta("branding_version", "2")
