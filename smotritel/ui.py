# экраны
import hashlib
import html
import json
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from .db import now
from .ai import AIUnavailable

PAGE = 6


def safe(value, limit=700):
    parts = []
    used = 0
    for char in str(value or "—"):
        escaped = html.escape(char)
        if used + len(escaped) > limit:
            break
        parts.append(escaped)
        used += len(escaped)
    return "".join(parts)


def button(text, data):
    if not 1 <= len(data.encode()) <= 64:
        raise ValueError("Callback too long")
    return {"text": text[:60], "callback_data": data}


class UI:
    def __init__(self, service):
        self.s, self.db = service, service.db

    def t(self, ru, en):
        return en if self.db.meta("lang", "ru") == "en" else ru

    def setting(self, name, default=True):
        return self.db.meta("setting:" + name, "1" if default else "0") == "1"

    def stamp(self, value):
        zone = self.db.meta("timezone", getattr(self.s.c, "timezone", "America/Montevideo"))
        try:
            tz = ZoneInfo(zone)
        except (ValueError, KeyError):
            tz = timezone.utc
        return datetime.fromtimestamp(value or now(), tz).strftime("%d.%m · %H:%M")

    def totals(self):
        scope = "((m.connection_id='dm:'||? AND m.chat_id=?) OR EXISTS(SELECT 1 FROM connections c WHERE c.id=m.connection_id AND c.owner_id=?))"
        params = (self.s.owner, self.s.owner, self.s.owner)
        res = self.db.one(
            f"SELECT count(*) messages,coalesce(sum(deleted_at IS NOT NULL),0) deleted,coalesce(sum(EXISTS(SELECT 1 FROM versions v WHERE v.connection_id=m.connection_id AND v.chat_id=m.chat_id AND v.message_id=m.message_id AND v.kind='edit')),0) edited FROM messages m WHERE {scope}",
            params,
        )
        res["files"] = self.db.one(
            f"SELECT count(DISTINCT f.path) n FROM media f JOIN versions v ON f.version_id=v.id JOIN messages m USING(connection_id,chat_id,message_id) WHERE f.state='ready' AND {scope}",
            params,
        )["n"]
        res["saved"] = self.db.one(
            f"SELECT count(*) n FROM saved s JOIN messages m USING(connection_id,chat_id,message_id) WHERE {scope}",
            params,
        )["n"]
        return res

    def nav(self, back="menu"):
        return [button(self.t("‹ Назад", "‹ Back"), back), button(self.t("⌂ Главная", "⌂ Home"), "menu")]

    def card(self, text, rows, target=None, new=False):
        if not self.s.active():
            return
        if new:
            markup = self.s.lower.current()
        else:
            screen = getattr(self.s, "screen", "context")
            markup = (
                self.s.lower.main()
                if screen == "menu"
                else self.s.lower.more()
                if screen == "more"
                else self.s.lower.set(rows, screen)
            )
        body = {
            "chat_id": self.s.owner,
            "text": text[:3900],
            "parse_mode": "HTML",
            "reply_markup": markup,
            "link_preview_options": {"is_disabled": True},
            "_ui_menu": not new,
            "_lower_keyboard": True,
        }
        if len(text) > 3900:

            class Plain(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self.parts = []

                def handle_data(self, data):
                    self.parts.append(data)

            parser = Plain()
            parser.feed(text)
            body["text"] = "".join(parser.parts)[:3900]
            body.pop("parse_mode")
        self.s.part += 1
        self.db.enqueue(f"{self.s.event}:{self.s.part}", "sendMessage", body)

    def message(self, rowid):
        row = self.db.one("SELECT rowid AS rid,* FROM messages WHERE rowid=?", (rowid,))
        if row and (
            row["connection_id"] == self.s.dm
            and row["chat_id"] == self.s.owner
            or self.db.one(
                "SELECT id FROM connections WHERE id=? AND owner_id=?", (row["connection_id"], self.s.owner)
            )
        ):
            return row
        return None

    def identity(self, m):
        u = self.db.one(
            "SELECT profile FROM users WHERE connection_id=? AND id=?", (m["connection_id"], m["sender_id"])
        )
        user = json.loads(u["profile"]) if u else {}
        name = (
            " ".join(user.get(k, "") for k in ("first_name", "last_name")).strip()
            or "Неизвестный отправитель"
        )
        c = self.db.one(
            "SELECT metadata FROM chats WHERE connection_id=? AND id=?", (m["connection_id"], m["chat_id"])
        )
        chat = json.loads(c["metadata"]) if c else {}
        chatname = (
            chat.get("title")
            or " ".join(chat.get(k, "") for k in ("first_name", "last_name")).strip()
            or "Личный чат"
        )
        return (
            f"👤 {safe(name, 90)}"
            + (" · @" + safe(user["username"], 40) if user.get("username") else "")
            + f"\n💬 {safe(chatname, 90)} · {self.stamp(m['sent_at'] or m['first_seen'])}"
        )

    def detail(self, m, kind="message", page=0):
        versions = self.db.history(m["connection_id"], m["chat_id"], m["message_id"])
        rid = m["rid"]
        rows = []
        heading = {
            "delete": "🗑 Удалённое сообщение",
            "edit": "✏️ Изменённое сообщение",
            "message": "◈ Сообщение",
            "history": "◷ История изменений",
        }[kind]
        text = f"<b>{heading}</b>\n{self.identity(m)}\n\n"
        if not versions:
            text += "Первоначальная версия не получена Telegram не передал оригинал; локальной копии нет"
        elif kind == "edit":
            before = versions[-2] if len(versions) > 1 else None
            text += "<b>Было</b>\n" + safe(
                (before["text"] or before["caption"] or "[медиа]")
                if before
                else "Предыдущая версия не получена",
                950,
            )
            text += "\n\n<b>Стало</b>\n" + safe(
                versions[-1]["text"] or versions[-1]["caption"] or "[медиа]", 950
            )
        elif kind == "history":
            total = len(versions)
            page = min(max(0, page), max(0, (total - 1) // 3))
            for v in versions[page * 3 : page * 3 + 3]:
                text += (
                    f"<b>Версия {v['sequence'] + 1}</b> · {self.stamp(v['observed_at'])}\n"
                    + safe(v["text"] or v["caption"] or "[медиа]", 750)
                    + "\n\n"
                )
            paging = []
            if page:
                paging.append(button("‹ Раньше", f"h:{rid}:{page - 1}"))
            if (page + 1) * 3 < total:
                paging.append(button("Позже ›", f"h:{rid}:{page + 1}"))
            if paging:
                rows.append(paging)
        else:
            orig = next((v for v in versions if v["kind"] == "original"), versions[0])
            label = "Полученный оригинал" if m["original_observed"] else "Самая ранняя доступная версия"
            text += f"<b>{label}</b>\n" + safe(
                orig["text"] or orig["caption"] or "[Медиа / служебное сообщение]", 1900
            )
        if versions and not m["original_observed"]:
            text += "\n\n⚠ Первоначальная версия не получена; показана самая ранняя доступная"
        if m["deleted_at"] and versions:
            text += "\n\nЛокальная история сохранена после удаления"
        media = self.db.all(
            "SELECT kind,state FROM media WHERE version_id IN (SELECT id FROM versions WHERE connection_id=? AND chat_id=? AND message_id=?)",
            (m["connection_id"], m["chat_id"], m["message_id"]),
        )
        if media:
            states = {
                "ready": "сохранено",
                "pending": "ожидает загрузки" if self.setting("media_save") else "автозагрузка выключена",
                "too_large": "превышен лимит",
                "budget_exceeded": "недостаточно места",
                "failed": "недоступно",
            }
            text += "\n\n📎 " + ", ".join(
                sorted(set(f"{x['kind']}: {states.get(x['state'], 'недоступно')}" for x in media))[:8]
            )
            rows.append(
                [button("↓ Медиа", f"f:{rid}")]
                + ([button("◷ История", f"h:{rid}:0")] if kind != "history" else [])
            )
        elif kind != "history":
            rows.append([button("◷ История", f"h:{rid}:0")])
        if any(v["text"] or v["caption"] for v in versions):
            rows.append([button("↓ Полный текст и версии", f"txt:{rid}")])
        saved = self.db.one(
            "SELECT message_id FROM saved WHERE connection_id=? AND chat_id=? AND message_id=?",
            (m["connection_id"], m["chat_id"], m["message_id"]),
        )
        rows.append([button("✓ Сохранено" if saved else "☆ Сохранить", f"save:{rid}")])
        rows.append([button("💬 Цитата", f"perform:q:{rid}")])
        if any(x["kind"] == "photo" for x in media):
            rows.append([button("🖼 Stories 3×3", f"perform:story:{rid}")])
        if any(x["kind"] in ("voice", "audio", "video_note") for x in media):
            rows.append([button("🎙 Расшифровать и кратко", f"sum:{rid}")])
        if self.setting("debug", False):
            text += f"\n\n<code>{safe(m['connection_id'])} / {m['chat_id']} / {m['message_id']}</code>"
        rows.append(self.nav(f"m:{rid}" if kind == "history" else self.db.meta("archive_route", "a:all:0")))
        return text, rows

    def notification(self, cid, chat, mid, kind):
        m = self.db.one(
            "SELECT rowid rid,* FROM messages WHERE connection_id=? AND chat_id=? AND message_id=?",
            (cid, chat, mid),
        )
        text, rows = self.detail(m, kind)
        self.card(text, rows, new=True)

    async def show(self, route="menu", target=None):
        route = {
            "start": "menu",
            "archive": "a:all:0",
            "saved": "a:saved:0",
            "functions": "help",
            "support": "privacy",
        }.get(route, route)
        self.s.screen = route
        parts = route.split(":")
        section = parts[0]
        from .features import ROUTES

        if section in ROUTES or route == "help":
            await self.s.features.show("catalog" if route == "help" else route, target)
            return
        rows = []
        text = ""
        if section == "menu":
            stats = self.totals()
            state = self.db.meta("runtime_status", "starting")
            health = json.loads(self.db.meta("local_health", "{}"))

            def status(key):
                return "🟢" if health.get(key) else "⚪"

            connected = self.db.one(
                "SELECT count(*) n FROM connections WHERE owner_id=? AND enabled=1", (self.s.owner,)
            )["n"]
            text = self.t(
                "<b>Смотритель</b>\nИстория остаётся с вами\n\nПолученные сообщения, версии после правок и доступные медиа хранятся на сервере Смотрителя",
                "<b>Smotritel</b>\nYour history stays with you\n\nReceived messages, edit history and available media stay on the Smotritel server",
            )
            text += f"\n\n{'🟢' if state == 'running' else '⚪'} Telegram · {self.t('онлайн' if state == 'running' else 'подключается', state)}\n🟢 {self.t('Локальный архив', 'Local archive')} · {stats['messages']}\n{status('ai')} Local AI · {safe(self.s.c.ai_model)}\n{status('stt')} {self.t('Локальная речь', 'Local speech')}\n{'🟢' if connected else '⚪'} Business · {self.t('подключён' if connected else 'ожидает подключения', 'connected' if connected else 'not connected')}"
            rows = [
                [button(self.t("🧩 Все функции", "🧩 All features"), "catalog")],
                [button("Как это работает", "privacy")],
            ]
        elif section == "onboard":
            text = "<b>Смотритель</b>\nИстория сообщений остаётся с вами\n\nВаш аккаунт создан Подключите бота к Telegram Business, чтобы сохранять полученные сообщения, правки и удаления Данные хранятся на сервере Смотрителя"
            rows = [[button("Начать · подключение", "connect")], [button("Как это работает", "privacy")]]
        elif section == "more":
            text = "<b>Ещё</b>\nВсе дополнительные действия — на нижней панели Прокрутите клавиатуру и выберите нужное"
            rows = [
                [button("🧩 Все функции", "catalog"), button("Плагины", "plugins")],
                [button("Premium · DEMO", "premium"), button("API", "api")],
                [button("Язык", "locale")],
                self.nav(),
            ]
        elif section == "tools":
            text = "<b>Инструменты</b>\nВыберите действие Отменить ввод можно нижней кнопкой «Отмена»"
            rows = [
                [button("Перевод", "input:translate"), button("Погода", "input:weather")],
                [button("История профиля", "input:search"), button("AFK текст", "input:afk")],
                [button("Справка", "help:tools")],
                [button("💗 Сердце", "fn:heart"), button("🎬 TikTok", "fn:tiktok")],
                [button("🧾 Демо Send", "fn:send"), button("Все функции", "catalog")],
                self.nav("more"),
            ]
        elif section == "input":
            mode = parts[1]
            if mode not in ("translate", "weather", "search", "afk"):
                raise ValueError("Unknown state")
            self.db.set_meta("input_mode", mode)
            self.s.prompt(
                {
                    "translate": "Введите текст для перевода на русский",
                    "weather": "Введите город",
                    "search": "Введите @username или ответьте /search на сообщение",
                    "afk": "Введите текст AFK Он будет применяться к вашим подключённым чатам",
                }[mode],
            )
            return
        elif section == "delete_data":
            text = "<b>Удалить мои данные?</b>\nБудут удалены ваш архив, медиа, настройки, профиль и подключение Данные других пользователей останутся Отправленные копии в Telegram и резервные копии сервера не удаляются этим действием\n\nПосле удаления отключите бота в Telegram Business"
            self.db.set_meta(
                "delete_nonce", hashlib.sha256((str(now()) + self.s.gen).encode()).hexdigest()[:12]
            )
            rows = [
                [button("Нет", "privacy"), button("Да, удалить", "erase:" + self.db.meta("delete_nonce"))]
            ]
        elif section == "erase":
            import secrets

            if (
                len(parts) != 2
                or not self.db.meta("delete_nonce")
                or not secrets.compare_digest(parts[1], self.db.meta("delete_nonce"))
            ):
                raise ValueError("Confirmation expired")
            from .privacy import erase

            await erase(self.s)
            return
        elif section == "tz":
            choices = ("America/Montevideo", "Europe/Moscow", "Europe/Berlin", "Asia/Almaty", "UTC")
            if len(parts) > 1:
                if parts[1] not in choices:
                    raise ValueError("Invalid timezone")
                self.db.set_meta("timezone", parts[1])
            text = "<b>Часовой пояс</b>\nСейчас: " + safe(self.db.meta("timezone", self.s.c.timezone))
            rows = [[button(z, "tz:" + z)] for z in choices] + [self.nav("settings")]
        elif section == "connect":
            conns = self.db.all("SELECT * FROM connections WHERE owner_id=?", (self.s.owner,))
            stats = self.totals()
            last = self.db.meta("last_business_update")
            text = (
                "<b>◉ Подключение</b>\n\n"
                + (
                    "🟢 Telegram: онлайн"
                    if self.db.meta("runtime_status") == "running"
                    else "⚪ Telegram: состояние проверяется"
                )
                + "\n✅ Аккаунт зарегистрирован"
            )
            for c in conns:
                rights = json.loads(c["rights"])
                text += (
                    "\n\n"
                    + ("🟢 Business активен" if c["enabled"] else "⚪ Business отключён")
                    + "\nОтветы в чат: "
                    + ("разрешены" if rights.get("can_reply") else "не разрешены")
                )
                for key, label in (
                    ("can_manage_stories", "Публикация stories"),
                    ("can_change_name", "Изменение имени"),
                    ("can_change_bio", "Изменение bio"),
                ):
                    text += f"\n{label}: {'разрешено' if rights.get(key) else 'не разрешено'}"
            if not conns:
                text += (
                    "\n⚪ Business ещё не подключён\n\nTelegram → Настройки → Telegram Business → Чат-боты Выберите @"
                    + safe(self.db.meta("bot_username", "ваш_бот"))
                    + " и нужные чаты Для автоответов разрешите ответы"
                )
            text += f"\n\nПоследнее Business-событие: {self.stamp(int(last)) if last else 'пока нет'}\nАрхив: {stats['messages']} сообщений · {stats['files']} файлов\n\nАрхив начинается с событий, которые Telegram передаёт после подключения. Для архива права на управление аккаунтом не нужны.\n\nОтветы, stories и часы включаются отдельными правами: Telegram → Настройки → Telegram Business → Чат-боты → этот бот."
            rows = [[button("↻ Обновить", "connect")], self.nav()]
        elif section in ("a", "media"):
            if section == "media":
                text = "<b>📎 Медиа</b>\nВыберите тип Доступны только файлы, переданные Telegram"
                rows = [
                    [button("Все файлы", "a:media:0"), button("Фото", "a:photo:0")],
                    [button("Голосовые", "a:voice:0"), button("Видео", "a:video:0")],
                    [button("Документы", "a:document:0")],
                    self.nav(),
                ]
            else:
                mode = parts[1] if len(parts) > 1 else "all"
                page = max(0, min(100000, int(parts[2]) if len(parts) > 2 else 0))
                filters = {
                    "all": "1",
                    "deleted": "m.deleted_at IS NOT NULL",
                    "edited": "EXISTS(SELECT 1 FROM versions v WHERE v.connection_id=m.connection_id AND v.chat_id=m.chat_id AND v.message_id=m.message_id AND v.kind='edit')",
                    "saved": "EXISTS(SELECT 1 FROM saved s WHERE s.connection_id=m.connection_id AND s.chat_id=m.chat_id AND s.message_id=m.message_id)",
                }
                params = [self.s.owner, self.s.owner, self.s.owner]
                scope = "((m.connection_id='dm:'||? AND m.chat_id=?) OR EXISTS(SELECT 1 FROM connections c WHERE c.id=m.connection_id AND c.owner_id=?))"
                if mode in ("media", "photo", "voice", "video", "document"):
                    cond = (
                        "EXISTS(SELECT 1 FROM versions v JOIN media f ON f.version_id=v.id WHERE v.connection_id=m.connection_id AND v.chat_id=m.chat_id AND v.message_id=m.message_id"
                        + (" AND f.kind=?" if mode != "media" else "")
                        + ")"
                    )
                    if mode != "media":
                        params.append(mode)
                else:
                    cond = filters.get(mode, "1")
                    mode = mode if mode in filters else "all"
                total = self.db.one(f"SELECT count(*) n FROM messages m WHERE {scope} AND {cond}", params)[
                    "n"
                ]
                page = min(page, max(0, (total - 1) // PAGE))
                items = self.db.all(
                    f"""SELECT m.rowid rid,m.*,(SELECT coalesce(text,caption) FROM versions v WHERE v.connection_id=m.connection_id AND v.chat_id=m.chat_id AND v.message_id=m.message_id ORDER BY sequence LIMIT 1) preview FROM messages m WHERE {scope} AND {cond} ORDER BY m.first_seen DESC,m.rowid DESC LIMIT ? OFFSET ?""",
                    (*params, PAGE, page * PAGE),
                )
                self.db.set_meta("archive_route", f"a:{mode}:{page}")
                title = {
                    "all": "Архив",
                    "deleted": "Удалённые",
                    "edited": "Изменённые",
                    "saved": "Сохранённые",
                    "media": "Медиа",
                    "photo": "Фото",
                    "voice": "Голосовые",
                    "video": "Видео",
                    "document": "Документы",
                }[mode]
                text = (
                    f"<b>◈ {title}</b> · {total}\n"
                    + (
                        f"Страница {page + 1} из {max(1, (total + PAGE - 1) // PAGE)}"
                        if total
                        else "Здесь пока пусто Полученные сообщения появятся автоматически"
                    )
                    + "\n"
                )
                for n, m in enumerate(items, 1):
                    preview = (m["preview"] or "[Медиа / оригинал не получен]").replace("\n", " ")
                    text += f"\n{n}. {'🗑 ' if m['deleted_at'] else ''}{safe(preview, 100)}\n{self.stamp(m['sent_at'] or m['first_seen'])}\n"
                    rows.append([button(f"{n} · {preview[:42]}", f"m:{m['rid']}")])
                paging = []
                if page:
                    paging.append(button("‹ Предыдущие", f"a:{mode}:{page - 1}"))
                if (page + 1) * PAGE < total:
                    paging.append(button("Следующие ›", f"a:{mode}:{page + 1}"))
                if paging:
                    rows.append(paging)
                rows.append([button("Все", "a:all:0"), button("☆ Сохранённые", "a:saved:0")])
                rows.append(self.nav())
        elif section in ("m", "h", "save", "f", "sum", "txt"):
            m = self.message(int(parts[1]))
            if not m:
                text = "⚪ Сообщение недоступно Возможно, локальная копия удалена"
                rows = [self.nav("a:all:0")]
            else:
                if section == "save":
                    self.db.run(
                        "INSERT OR IGNORE INTO saved VALUES(?,?,?,?)",
                        (m["connection_id"], m["chat_id"], m["message_id"], now()),
                    )
                if section == "txt":
                    history = self.db.history(m["connection_id"], m["chat_id"], m["message_id"])
                    data = (
                        "Смотритель · экспорт полученных версий\nВремя: "
                        + self.db.meta("timezone", "America/Montevideo")
                        + "\n\n"
                        + "\n\n".join(
                            f"Версия {v['sequence'] + 1} · {self.stamp(v['observed_at'])}\n{v['text'] or v['caption'] or '[Медиа / служебное сообщение]'}"
                            for v in history
                        )
                    )
                    body = data.encode("utf-8")
                    if len(body) > self.s.c.max_media:
                        raise ValueError("Export too large")
                    folder = self.s.media.generated
                    folder.mkdir(exist_ok=True, mode=0o700)
                    path = folder / ("message-" + hashlib.sha256(body).hexdigest()[:20] + ".txt")
                    path.write_bytes(body)
                    path.chmod(0o600)
                    self.s.file(self.s.owner, path, filename="История сообщения.txt")
                    text = "✅ Полный текст и все полученные версии отправлены файлом"
                    rows = [self.nav(f"m:{m['rid']}")]
                elif section in ("f", "sum"):
                    history = self.db.history(m["connection_id"], m["chat_id"], m["message_id"])
                    media = await self.s.media.ensure([v["id"] for v in history])
                    ready = [x for x in media if x["state"] == "ready"]
                    if section == "f":
                        for item in ready:
                            self.s.file(self.s.owner, self.s.media.path(item))
                        text = "📎 " + (
                            "Сохранённые файлы отправлены отдельными сообщениями"
                            if ready
                            else "Локальных файлов нет Telegram мог не передать файл, либо превышен лимит хранения"
                        )
                        rows = [self.nav(f"m:{m['rid']}")]
                    else:
                        audio = next(
                            (x for x in reversed(ready) if x["kind"] in ("voice", "audio", "video_note")),
                            None,
                        )
                        if not audio:
                            text = "⚪ Нужен локально сохранённый голосовой файл до 20 МБ"
                        else:
                            self.s.send(
                                self.s.owner, "🎙 Распознаю речь локально Это может занять несколько секунд…"
                            )
                            txt = await self.s.ai.transcript(self.s.media.path(audio))
                            if not txt.strip():
                                text = "⚪ Речь не распознана Попробуйте запись с более чётким звуком"
                            else:
                                try:
                                    res = await self.s.ai.answer(
                                        txt,
                                        "Кратко перескажи только эту расшифровку на её языке Не добавляй имена, даты, числа и факты, которых нет в тексте Если текст неясен, скажи об этом",
                                    )
                                    label = "📝 Кратко (AI; сверяйте с расшифровкой)"
                                    if len(txt) > 6000:
                                        label += " · по первым 6000 символам"
                                    self.s.send(
                                        self.s.owner,
                                        "🎙 Расшифровка\n" + txt + "\n\n" + label + "\n" + res,
                                    )
                                except AIUnavailable:
                                    logging.getLogger("smotritel").exception("Voice summary AI failed")
                                    self.s.send(
                                        self.s.owner,
                                        "🎙 Расшифровка\n"
                                        + txt
                                        + "\n\n⚪ Краткий итог сейчас недоступен; расшифровка сохранена в этом ответе",
                                    )
                                text = "✅ Расшифровка и краткий итог готовы"
                        rows = [self.nav(f"m:{m['rid']}")]
                else:
                    text, rows = self.detail(
                        m, "history" if section == "h" else "message", int(parts[2]) if len(parts) > 2 else 0
                    )
                    if section == "save":
                        text += "\n\n☆ Добавлено в сохранённые"
        elif section == "profile":
            stats = self.totals()
            p = json.loads(self.db.meta("owner_profile", "{}"))
            name = " ".join(p.get(k, "") for k in ("first_name", "last_name")).strip() or "Ваш аккаунт"
            active = self.db.one(
                "SELECT count(*) n FROM connections WHERE owner_id=? AND enabled=1", (self.s.owner,)
            )["n"]
            text = (
                f"<b>◉ {safe(name, 100)}</b>"
                + ("\n@" + safe(p["username"], 40) if p.get("username") else "")
                + f"\n✅ Аккаунт · {self.db.meta('lang', 'ru').upper()}\n\nАрхив: {stats['messages']}\nУдалённые: {stats['deleted']} · Изменённые: {stats['edited']}\nСохранённые: {stats['saved']} · Файлы: {stats['files']}\n\nAI: {safe(self.s.c.ai_provider)} · {safe(self.s.c.ai_model)}\nРечь: локальный Whisper tiny\nBusiness: {'активен' if active else 'не подключён'}\nСессия: {max(0, (now() - int(self.db.meta('started_at', now()))) // 60)} мин."
            )
            acct = self.db.one("SELECT registered_at FROM accounts WHERE id=?", (self.s.owner,))
            text += "\nРегистрация: " + self.stamp(acct["registered_at"])
            rows = [
                [button("Настройки", "settings"), button("Подключение", "connect")],
                [button("Premium · DEMO", "premium"), button("Плагины", "plugins")],
                [button("API", "api"), button("Язык", "locale")],
                self.nav(),
            ]
        # настройки
        elif section in ("settings", "toggle", "language"):
            allowed = {
                "delete_notifications": True,
                "edit_notifications": True,
                "media_save": True,
                "afk": False,
                "autoreply": False,
                "debug": False,
            }
            if section == "toggle" and len(parts) == 2 and parts[1] in allowed:
                key = parts[1]
                with self.db.transaction():
                    if not self.db.one(
                        "SELECT update_id FROM processed_actions WHERE tenant_id=? AND update_id=?",
                        (self.s.owner, self.s.update_id),
                    ):
                        self.db.run(
                            "INSERT INTO processed_actions VALUES(?,?,?)",
                            (self.s.owner, self.s.update_id, now()),
                        )
                        self.db.set_meta("setting:" + key, "0" if self.setting(key, allowed[key]) else "1")
            if section == "language" and parts[1] in ("ru", "en"):
                self.db.set_meta("lang", parts[1])
                self.s.lower.main()
                self.s.send(self.s.owner, "✅ Язык: RU" if parts[1] == "ru" else "✅ Language: EN")
            text = self.t(
                "<b>⚙ Настройки</b>\nИзменения сохраняются на сервере Смотрителя\n\nAFK и AI применяются к новым Business-сообщениям Команды в чате имеют приоритет",
                "<b>⚙ Settings</b>\nChanges are stored on the Smotritel server\n\nAFK and AI apply to new Business messages. Chat commands take priority",
            )
            for key, ru, en in [
                ("delete_notifications", "Удаления", "Deletion alerts"),
                ("edit_notifications", "Изменения", "Edit alerts"),
                ("media_save", "Автозагрузка медиа", "Auto-download media"),
                ("afk", "AFK", "AFK"),
                ("autoreply", "AI автоответы", "AI auto-replies"),
                ("debug", "Технические ID", "Technical IDs"),
            ]:
                rows.append(
                    [
                        button(
                            ("● " if self.setting(key, allowed[key]) else "○ ") + self.t(ru, en),
                            f"toggle:{key}",
                        )
                    ]
                )
            rows.extend(
                [
                    [button("Русский", "language:ru"), button("English", "language:en")],
                    [
                        button(self.t("Приватность", "Privacy"), "privacy"),
                        button(self.t("Хранение", "Retention"), "retention"),
                    ],
                    [button("Часовой пояс", "tz")],
                    self.nav(),
                ]
            )
        elif section == "retention":
            text = (
                "<b>◷ Хранение</b>\n\nИстория хранится на сервере Смотрителя, пока вы не удалите свои данные Автоудаление архива выключено\n\nСозданные цитаты и story очищаются через 7 дней Исходные медиа остаются\nЛимит медиа вашего аккаунта: "
                + str(self.s.c.tenant_media_budget // 1024 // 1024)
                + " МБ Один файл: "
                + str(self.s.c.max_media // 1024 // 1024)
                + " МБ\n\nУдалить свой архив можно в разделе «Приватность». Резервными копиями сервера управляет его администратор"
            )
            rows = [self.nav("settings")]
        elif section == "privacy":
            text = (
                "<b>🔐 Ваши данные</b>\n\nСообщения и файлы хранятся на сервере Смотрителя для работы архива Другие пользователи не имеют к ним доступа AI и распознавание речи работают на этом сервере; внешний AI выключен по умолчанию Telegram получает ответы и запрошенные вами файлы\n\nВнешний текстовый AI: "
                + ("разрешён конфигурацией" if self.s.c.external_ai else "выключен")
                + "\nПогода: "
                + ("включена" if self.s.c.weather else "выключена")
                + " · TikTok: "
                + ("включён" if self.s.c.tiktok else "выключен")
                + "\n\nНеполученные сообщения восстановить нельзя В Telegram могут оставаться отправленные копии"
            )
            rows = [[button("Удалить мои данные", "delete_data")], self.nav("settings")]
        elif section == "ai":
            health = json.loads(self.db.meta("local_health", "{}"))
            text = (
                "<b>✦ Локальный AI</b>\n"
                + safe(self.s.c.ai_model)
                + " · "
                + ("🟢 модель готова" if health.get("ai") else "⚪ готовность ещё не подтверждена")
                + "\n\nЗадайте вопрос или получите расшифровку голосового Ответы модели могут содержать ошибки; сверяйте важные факты"
            )
            rows = [
                [button("Задать вопрос", "ask")],
                [button("🎙 Голосовое → кратко", "voice")],
                [button("Автоответы", "settings"), button("О модели", "help:ai")],
                self.nav(),
            ]
        elif section in ("ask", "voice", "photo", "cancel"):
            if section == "cancel":
                self.db.set_meta("input_mode", "")
                self.s.send(self.s.owner, "Действие отменено Выберите раздел")
                return
            self.db.set_meta("input_mode", section)
            self.s.prompt(
                "🤖 Напишите вопрос Любая кнопка меню отменит ожидание"
                if section == "ask"
                else "🖼 Пришлите фото для цитаты или stories"
                if section == "photo"
                else "🎙 Пришлите голосовое или аудио Можно также ответить .summary на запись в Business-чате",
            )
            return
        elif section == "stats":
            stats = self.totals()
            text = f"<b>▥ Статистика архива</b>\n\nСообщений: {stats['messages']}\nУдалённых: {stats['deleted']}\nС правками: {stats['edited']}\nСохранённых: {stats['saved']}\nСкачанных файлов: {stats['files']}\n\nУчитываются только события, полученные ботом. Для отчёта по Business-чату: .stat или .wrapped 30."
            rows = [[button("Открыть архив", "a:all:0")], self.nav()]
        elif section == "help":
            categories = {
                "archive": (
                    "Архив",
                    "Полученные сообщения и все версии остаются локально\n\n.save — ответом сохранить сообщение\n/history — версии (техническая команда)\n/media — скачать копии\n\nКнопки архива позволяют делать это без ID",
                ),
                "business": (
                    "Business",
                    "В подключённом чате используйте команды владельца:\n\n.afk текст / .unafk\n.autoreply on|off\n.echo on|off · .troll on|off\n.mute 5m / .unmute\n\nАвтоответы: максимум один в минуту на чат Mute приостанавливает ответы бота",
                ),
                "ai": (
                    "AI",
                    "Вопрос: .ai вопрос\nПеревод: .translate ru текст\nГолосовое: reply → .summary\n\nWhisper tiny распознаёт речь локально Ошибки распознавания и модели возможны Краткий итог сверяйте с расшифровкой",
                ),
                "media": (
                    "Медиа",
                    "Reply на сообщение:\n\n.q [1–3] — карточка цитаты\n.story — фото 1080×1920\n.story grid — сетка 3×3\n.summary — голосовое\n\n.tiktok ссылка — только при локальном включении загрузок До 20 МБ",
                ),
                "stats": (
                    "Статистика",
                    ".stat — отчёт по чату\n.wrapped 30 — последние 30 дней\n.search @user — наблюдаемая история профиля\n.watch @user · .watch list\n.watch stop @user\n\nДанные до начала наблюдения недоступны",
                ),
                "tools": (
                    "Инструменты",
                    ".weather город — погода, opt-in\n.heart — сердечко\n.send USDT 0.03 — только DEMO\n.spam 1–3 текст — собственный тестовый чат\n\nЛокальный API: /api Резервное копирование и очистка — через README",
                ),
                "connect": (
                    "Помощь с подключением",
                    "1. Нажмите Start в чате со Смотрителем\n2. Telegram → Настройки → Telegram Business → Чат-боты\n3. Выберите @SmotritelGess_bot и нужные чаты\n\nДля регистрации достаточно /start Для автоответов разрешите ответы Старые сообщения Telegram не передаёт",
                ),
            }
            if len(parts) > 1 and parts[1] in categories:
                title, body = categories[parts[1]]
                text = f"<b>◈ {title}</b>\n\n{safe(body, 3000)}"
                rows = [self.nav("help")]
            else:
                text = self.t(
                    "<b>◈ Все функции</b>\nВыберите раздел Команды выполняются владельцем в подключённом чате",
                    "<b>◈ All features</b>\nChoose a category. Commands run for the owner in connected chats",
                )
                rows = [
                    [button("Архив", "help:archive"), button("Business", "help:business")],
                    [button("AI", "help:ai"), button("Медиа", "help:media")],
                    [button("Статистика", "help:stats"), button("Инструменты", "help:tools")],
                    self.nav(),
                ]
        elif section in ("premium", "plugins"):
            text = "<b>Смотритель · Local Edition</b>\nВсе локальные функции доступны бесплатно Покупок и подписок нет Архив, AI, цитаты и наблюдение встроены в бота"
            rows = [self.nav("help")]
        else:
            text = "⚪ Этот экран устарел Вернитесь на главную"
            rows = [self.nav()]
        self.card(text, rows, target)

    async def navigate(self, route, target=None):
        try:
            if route.startswith("sum:") and not self.s.job_mode:
                msg = self.message(int(route.split(":")[1]))
                if msg:
                    self.s.queue_job("ui", {"route": route}, "summary")
                else:
                    self.s.send(self.s.owner, "Сообщение недоступно в вашем архиве")
                return
            if not self.s.job_mode and not route.startswith(("ask", "voice", "input:")):
                self.db.set_meta("input_mode", "")
            if not route.startswith(("ask", "voice", "input:", "more", "erase:", "cancel")):
                self.s.ensure_keyboard()
            await self.show(route, target)
        except (AIUnavailable, ValueError, OverflowError, IndexError, KeyError, OSError):
            logging.getLogger("smotritel").exception("Menu action failed")
            self.card(
                "⚪ Не удалось выполнить действие Проверьте локальные сервисы и доступность файла",
                [self.nav()],
                target,
            )
