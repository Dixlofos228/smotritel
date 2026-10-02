#!/usr/bin/env python3

import getpass
import runpy
import os
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
os.umask(0o077)
(root / "secrets").mkdir(exist_ok=True)
token = getpass.getpass("BOT_TOKEN из BotFather (ввод скрыт): ").strip()
if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
    raise SystemExit("Неверный формат BOT_TOKEN; файл не изменён")
(root / "secrets/bot_token").write_text(token + "\n")
(root / "secrets/bot_token").chmod(0o600)
ai_key_path = root / "secrets/ai_api_key"
if not ai_key_path.exists():
    ai_key_path.write_text("\n")
ai_key_path.chmod(0o600)
env = root / ".env"
if not env.exists():
    env.write_text((root / ".env.example").read_text())
print("Секрет сохранён в secrets/bot_token. Запустите docker compose up -d --build")
(root / "secrets").chmod(0o700)
runpy.run_path(str(root / "scripts/prepare_connection.py"), run_name="__main__")
print("Для входа откройте бота в Telegram и нажмите Start. Каждому пользователю создаётся отдельный аккаунт")
