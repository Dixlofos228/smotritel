import html
import os
from pathlib import Path
import json
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]

    token = (root / "secrets/bot_token").read_text().strip()
    try:
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/getMe",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as resp:
            res = json.load(resp)
        if not res.get("ok"):
            raise ValueError("Telegram unavailable")
        me = res["result"]
    except Exception as error:
        print("Telegram connection unavailable: " + type(error).__name__ + "; secret values omitted")
        return
    username = me["username"]
    link = f"https://t.me/{username}"
    target = root / "secrets/connect.html"
    os.umask(0o077)
    target.write_text(
        '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Подключить Смотритель</title><style>body{background:#0c1b2b;color:#edf6f6;font:18px system-ui;max-width:620px;margin:12vh auto;padding:24px}a{display:inline-block;background:#4ce3c5;color:#0c1b2b;padding:18px 26px;border-radius:18px;text-decoration:none;font-weight:650}p{line-height:1.6;color:#afc5d7}</style><h1>Смотритель</h1><p>История остаётся с вами.</p><p>Откройте бота и нажмите Start. У каждого пользователя свой аккаунт и архив. Данные хранятся на сервере Смотрителя; внешний AI выключен по умолчанию.</p><a href="'''
        + html.escape(link, quote=True)
        + """">Открыть в Telegram</a><p>Затем: Настройки Telegram → Telegram Business → Чат-боты → @"""
        + html.escape(username)
        + """<br>Выберите нужные чаты и разрешите ответы, если нужны команды и автоответы.</p></html>"""
    )
    target.chmod(0o600)
    print("Public onboarding page created: secrets/connect.html")
    print(
        "Bot username: @"
        + username
        + "; Business capability: "
        + str(bool(me.get("can_connect_to_business")))
    )


if __name__ == "__main__":
    main()
