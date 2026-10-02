# функции
import json
from .db import now, dump
from .ui import button, safe

CATEGORIES = {
    "archive": ("🗂 Архив", "Сообщения, версии и сохранённые копии"),
    "media": ("🎨 Медиа", "Цитаты, stories и анимации"),
    "chat": ("💬 Чаты", "Автоответы выбранного Business-чата"),
    "ai": ("🤖 AI", "Вопросы, перевод и голосовые — локально"),
    "stats": ("📊 Наблюдение", "Статистика и изменения профилей"),
    "tools": ("🧰 Инструменты", "Погода, TikTok, демочек и интеграции"),
}

SPECS = {
    "save": (
        "archive",
        "💾 Сохранить",
        ".save",
        "Сообщение и полученные версии останутся в вашем личном архиве",
        "message",
    ),
    "history": ("archive", "◷ История", "История", "Просмотр всех полученных версий сообщения", "message"),
    "q": (
        "media",
        "💬 Цитата",
        ".q [1–3]",
        "PNG из текста или фото, до трёх полученных сообщений Результат придёт вам в личку",
        "message",
    ),
    # хуйня ебаная, хз что это и для чего
    "story": (
        "media",
        "🖼 Stories 3×3",
        ".story / .story single",
        "Девять кадров из фото Сначала предпросмотр, затем отдельная кнопка публикации в свои stories",
        "message",
    ),
    "heart": (
        "media",
        "💗 Сердце",
        ".heart",
        "Пульсирующее сердце в GIF Из меню анимация отправляется вам в личку",
        "direct",
    ),
    "echo": ("chat", "🪞 Эхо", ".echo [on|off]", "Повторять текст собеседника в выбранном чате", "chat"),
    "troll": (
        "chat",
        "🎲 Случайные ответы",
        ".troll [on|off]",
        "Короткие случайные ответы; не чаще одного автоответа в минуту",
        "chat",
    ),
    "antimute": (
        "chat",
        "📥 Зеркало исходящих",
        ".antimute [on|off]",
        "Копировать ваши исходящие сообщения в личку Смотрителя Ограничения Telegram этим не снимаются",
        "chat",
    ),
    "afk": (
        "chat",
        "🌙 AFK",
        ".afk [текст]",
        "Свой текст автоответа для выбранного чата, до отключения",
        "chat",
    ),
    "unafk": ("chat", "☀️ Отключить AFK", ".unafk", "Прекратить AFK-ответы в выбранном чате", "chat"),
    "mute": (
        "chat",
        "⏸ Пауза бота",
        ".mute [30s]",
        "Приостановить автоответы бота В личном чате бот не может ограничить сообщения собеседника",
        "chat",
    ),
    "unmute": ("chat", "▶️ Возобновить", ".unmute", "Снять паузу автоответов в выбранном чате", "chat"),
    "autoreply": (
        "ai",
        "💬 AI автоответы",
        ".autoreply [on|off]",
        "Включить или отключить локальные AI-ответы в выбранном чате",
        "chat",
    ),
    "ai": (
        "ai",
        "✦ Задать вопрос",
        ".ai вопрос",
        "Напишите вопрос для локальной Qwen Ответ придёт вам в личку",
        "input",
    ),
    "translate": (
        "ai",
        "🌐 Перевод",
        ".translate ru текст",
        "Выберите язык кнопкой и пришлите текст Перевод выполняется локально",
        "input",
    ),
    "summary": (
        "ai",
        "🎙 Голосовое → кратко",
        ".summary",
        "Расшифровка и краткий итог Выберите запись из архива или пришлите новую",
        "message",
    ),
    "stat": (
        "stats",
        "📊 Статистика чата",
        ".stat",
        "Сообщения, правки, удаления и активные участники выбранного чата",
        "chat",
    ),
    "wrapped": (
        "stats",
        "📅 Итоги периода",
        ".wrapped [дней]",
        "Отчёт за неделю, месяц или 90 дней",
        "chat",
    ),
    "search": (
        "stats",
        "🔎 История профиля",
        ".search id/@user",
        "Полученные изменения имени, username и доступного bio",
        "chat",
    ),
    "watch": (
        "stats",
        "👁 Наблюдать",
        ".watch",
        "Отслеживать профиль собеседника при новых сообщениях",
        "chat",
    ),
    "watch_list": (
        "stats",
        "📋 Наблюдаемые",
        ".watch list",
        "Ваш список с просмотром истории и остановкой",
        "direct",
    ),
    "screentime": (
        "stats",
        "🕒 Активность",
        ".screentime",
        "Активные часы по сообщениям за 30 дней Это не экранное время устройства",
        "chat",
    ),
    "weather": (
        "tools",
        "🌤 Погода",
        ".weather город",
        "Температура, влажность и ветер Город передаётся Open-Meteo",
        "input",
    ),
    "tiktok": (
        "tools",
        "🎬 TikTok",
        "TikTok link / .tiktok ссылка",
        "Видео и фото без водяного знака Публичная ссылка передаётся TikTok и загрузчику TikWM",
        "input",
    ),
    "send": (
        "tools",
        "🧾 Демо Send",
        ".send USDT 0.03",
        "Визуальный демочек с пометкой DEMO Деньги не переводятся",
        "input",
    ),
    "spam": (
        "tools",
        "🪞 Повтор в зеркале",
        ".spam 1–3 текст",
        "До трёх копий только в вашей личке с ботом, с интервалом две секунды",
        "input",
    ),
    "yandex": (
        "tools",
        "🔎 Яндекс",
        ".yandex запрос",
        "Поиск в интернете с ответом прямо в чате и источниками Запрос передаётся Bing, ответ готовит локальная Qwen",
        "input",
    ),
    "timename": (
        "tools",
        "🕒 Время в имени",
        ".timename on|off",
        "Время в имени Business-аккаунта Нужно право менять имя; отключение возвращает исходное имя",
        "connection",
    ),
    "timebio": (
        "tools",
        "🕒 Время в bio",
        ".timebio on|off",
        "Время в bio раз в минуту Нужно право менять bio; исходный текст сохраняется",
        "connection",
    ),
    "crash": (
        "tools",
        "🛠 Проверка ошибок",
        ".crash",
        "Тест ошибки в отдельном процессе Бот и Telegram-клиент продолжают работать",
        "direct",
    ),
}
ROUTES = {
    "launch",
    "catalog",
    "plugins",
    "cat",
    "fn",
    "begin",
    "pick",
    "run",
    "period",
    "setchat",
    "choose",
    "perform",
    "api",
    "apikey",
    "premium",
    "plan",
    "locale",
    "watchstop",
    "watchhistory",
    "accounts",
    "clock",
    "publishstory",
    "storystatus",
}


class Features:
    def __init__(self, service):
        self.s, self.db, self.ui = service, service.db, service.ui

    def chat(self, rid):
        return self.db.one(
            "SELECT h.rowid rid,h.* FROM chats h JOIN connections c ON c.id=h.connection_id WHERE h.rowid=? AND c.owner_id=? AND c.enabled=1",
            (int(rid), self.s.owner),
        )

    def chat_name(self, row):
        p = json.loads(row["metadata"])
        return (
            p.get("title")
            or " ".join(p.get(k, "") for k in ("first_name", "last_name")).strip()
            or "Личный чат"
        )

    def input(self, key, row=None, **extra):
        ctx = {"key": key, "expires": now() + 600, **extra}
        if row:
            ctx.update(cid=row["connection_id"], chat=row["id"])
        self.db.set_meta("feature_context", dump(ctx))
        self.db.set_meta("input_mode", "feature")
        prompt = {
            "ai": "Напишите вопрос для локального AI",
            "translate": "Пришлите текст для перевода",
            "weather": "Введите город Он будет отправлен Open-Meteo",
            "tiktok": "Пришлите публичную ссылку TikTok Загрузка без водяного знака; ссылка передаётся TikTok и TikWM",
            "send": "Введите валюту и сумму, например: USDT 0.03. Это только DEMO",
            "spam": "Введите количество и текст, например: 3 Привет Копии придут только вам",
            "yandex": "Введите запрос Найду информацию в интернете и отвечу здесь Поисковый запрос передаётся Bing",
            "afk": "Введите текст AFK для выбранного чата",
            "mute": "Введите длительность: 30s, 5m или 1h",
            "search": "Введите @username или ID наблюдаемого пользователя",
        }[key]
        self.s.prompt(prompt, "fn:" + key)

    async def consume(self, message):
        ctx = json.loads(self.db.meta("feature_context", "{}") or "{}")
        self.db.set_meta("feature_context", "")
        if ctx.get("expires", 0) < now() or ctx.get("key") not in SPECS:
            self.s.send(self.s.owner, "Ожидание истекло Выберите функцию заново")
            return
        key, text = ctx["key"], message.get("text", "").strip()
        src = {"chat": {"id": ctx.get("chat", self.s.owner)}}
        cid = ctx.get("cid")
        if cid and not self.db.owns(cid, src["chat"]["id"]):
            raise ValueError("Foreign input context")
        cmd = key + " " + text
        if key == "translate":
            cmd = "translate " + ctx.get("lang", "ru") + " " + text
        await self.s.command(src, cmd, cid, private=True)

    async def show(self, route, target=None):
        p = route.split(":")
        section = p[0]
        self.s.screen = route
        text, rows = "", []
        if section == "launch":
            key = p[1]
            if key not in SPECS:
                raise ValueError("Unknown feature")
            flow = SPECS[key][4]
            next_route = (
                "fn:" + key
                if key == "translate" or flow == "message"
                else "pick:" + key + ":0"
                if flow == "chat"
                else "accounts:" + key
                if flow == "connection"
                else "begin:" + key
            )
            await self.show(next_route, target)
            return
        if section in ("catalog", "plugins"):
            text = "<b>🧩 Все функции</b>\nВстроены в Смотритель · бесплатно\n\nВыберите категорию Результаты действий из меню открываются в вашей личке Автоответы включаются отдельно для выбранного чата"
            entries = [button(label, "cat:" + key) for key, (label, _) in CATEGORIES.items()]
            rows = [entries[i : i + 2] for i in range(0, len(entries), 2)] + [
                [button("Premium · DEMO", "premium"), button("API", "api")],
                self.ui.nav(),
            ]
        elif section == "cat":
            label, desc = CATEGORIES[p[1]]
            text = "<b>" + label + "</b>\n" + desc + "\n\nВыберите действие"
            entries = [button(v[1], "fn:" + k) for k, v in SPECS.items() if v[0] == p[1]]
            rows = [entries[i : i + 2] for i in range(0, len(entries), 2)] + [self.ui.nav("catalog")]
        elif section == "fn":
            key = p[1]
            cat, label, cmd, desc, flow = SPECS[key]
            text = f"<b>{label}</b>\n{desc}\n\nВ чате: <code>{safe(cmd)}</code>"
            if flow == "message":
                rows = [[button("Выбрать из архива", "choose:" + key + ":0")]]
                if key == "summary":
                    rows += [[button("Прислать голосовое", "voice")]]
                if key in ("story", "q"):
                    rows += [[button("Прислать фото", "photo")]]
            elif flow == "chat":
                rows = [[button("Выбрать свой чат", "pick:" + key + ":0")]]
            elif flow == "connection":
                rows = [[button("Выбрать аккаунт", "accounts:" + key)]]
            elif key == "translate":
                rows = [
                    [
                        button("На русский", "begin:translate:ru"),
                        button("На английский", "begin:translate:en"),
                    ],
                    [button("На испанский", "begin:translate:es")],
                ]
            else:
                rows = [[button("Начать", "begin:" + key)]]
            rows += [self.ui.nav("cat:" + cat)]
        elif section == "begin":
            key = p[1]
            if SPECS[key][4] == "input":
                self.input(key, lang=p[2] if len(p) > 2 else "ru")
            elif key == "watch_list":
                await self.watches(target)
            elif SPECS[key][4] == "direct":
                await self.s.command({"chat": {"id": self.s.owner}}, key, None, private=True)
            else:
                raise ValueError("Invalid direct action")
            return
        elif section == "pick":
            key, page = p[1], max(0, int(p[2]))
            if SPECS[key][4] != "chat":
                raise ValueError("Invalid chat action")
            count = self.db.one(
                "SELECT count(*) n FROM chats h JOIN connections c ON c.id=h.connection_id WHERE c.owner_id=? AND c.enabled=1",
                (self.s.owner,),
            )["n"]
            page = min(page, max(0, (count - 1) // 6))
            items = self.db.all(
                "SELECT h.rowid rid,h.* FROM chats h JOIN connections c ON c.id=h.connection_id WHERE c.owner_id=? AND c.enabled=1 ORDER BY h.rowid DESC LIMIT 6 OFFSET ?",
                (self.s.owner, page * 6),
            )
            text = f"<b>{SPECS[key][1]}</b>\nВыберите свой Business-чат."
            if not items:
                text += "\n\nПока нет полученных чатов Подключите Business и получите сообщение"
                rows = [[button("Подключение", "connect")]]
            rows += [[button(self.chat_name(r), f"run:{key}:{r['rid']}")] for r in items]
            rows += self.pages("pick:" + key, page, count) + [self.ui.nav("fn:" + key)]
        elif section in ("run", "period"):
            key = p[1]
            if SPECS[key][4] != "chat":
                raise ValueError("Invalid chat action")
            row = self.chat(p[2])
            if not row:
                raise ValueError("Chat unavailable")
            opts = self.db.settings(row["connection_id"], row["id"])
            text = f"<b>{SPECS[key][1]}</b>\n{safe(self.chat_name(row), 160)}\n\n"
            if key in ("echo", "troll", "antimute", "autoreply"):
                enabled = opts.get(key, self.ui.setting("autoreply", False) if key == "autoreply" else False)
                text += "Режим: " + ("включён" if enabled else "выключен")
                rows = [
                    [
                        button(
                            "Выключить" if enabled else "Включить",
                            f"setchat:{key}:{row['rid']}:{'off' if enabled else 'on'}",
                        )
                    ]
                ]
            elif key == "wrapped" and section == "run":
                text += "За какой период собрать итоги?"
                rows = [[button(f"{d} дней", f"period:wrapped:{row['rid']}:{d}") for d in (7, 30, 90)]]
            elif key in ("afk", "mute", "search"):
                self.input(key, row)
                return
            else:
                cmd = key + (" " + p[3] if section == "period" else "")
                await self.s.command({"chat": {"id": row["id"]}}, cmd, row["connection_id"], private=True)
                return
            rows += [self.ui.nav("fn:" + key)]
        elif section == "setchat":
            key = p[1]
            if key not in ("echo", "troll", "antimute", "autoreply") or p[3] not in ("on", "off"):
                raise ValueError("Invalid setting")
            row = self.chat(p[2])
            if not row:
                raise ValueError("Chat unavailable")
            await self.s.command(
                {"chat": {"id": row["id"]}}, key + " " + p[3], row["connection_id"], private=True
            )
            await self.show(f"run:{key}:{row['rid']}", target)
            return
        elif section == "choose":
            key, page = p[1], max(0, int(p[2]))
            if SPECS[key][4] != "message":
                raise ValueError("Invalid message action")
            condition = "1"
            if key in ("story", "summary"):
                types = "'photo'" if key == "story" else "'voice','audio','video_note'"
                condition = f"EXISTS(SELECT 1 FROM versions v JOIN media f ON f.version_id=v.id WHERE v.connection_id=m.connection_id AND v.chat_id=m.chat_id AND v.message_id=m.message_id AND f.kind IN ({types}))"
            scope = f"((m.connection_id=? AND m.chat_id=?) OR EXISTS(SELECT 1 FROM connections c WHERE c.id=m.connection_id AND c.owner_id=?)) AND {condition}"
            args = (self.s.dm, self.s.owner, self.s.owner)
            count = self.db.one(f"SELECT count(*) n FROM messages m WHERE {scope}", args)["n"]
            page = min(page, max(0, (count - 1) // 6))
            items = self.db.all(
                f"SELECT m.rowid rid,m.* FROM messages m WHERE {scope} ORDER BY m.rowid DESC LIMIT 6 OFFSET ?",
                (*args, page * 6),
            )
            text = f"<b>{SPECS[key][1]}</b>\nВыберите полученное сообщение."
            for n, m in enumerate(items, 1):
                v = self.db.history(m["connection_id"], m["chat_id"], m["message_id"])[-1:]
                preview = (v[0]["text"] or v[0]["caption"] or "Медиа") if v else "Оригинал не получен"
                rows += [[button(f"{n} · {preview[:38]}", f"perform:{key}:{m['rid']}")]]
            if not items:
                text += "\n\nПодходящих сообщений пока нет Получите сообщение или фото в подключённом чате"
            rows += self.pages("choose:" + key, page, count) + [self.ui.nav("fn:" + key)]
        elif section == "perform":
            key, rid = p[1], int(p[2])
            if key not in ("save", "q", "story", "summary", "history"):
                raise ValueError("Invalid message action")
            m = self.ui.message(rid)
            if not m:
                raise ValueError("Message unavailable")
            if key in ("save", "history"):
                await self.ui.show(
                    f"{'save' if key == 'save' else 'h'}:{rid}" + (":0" if key == "history" else ""), target
                )
                return
            versions = self.db.history(m["connection_id"], m["chat_id"], m["message_id"])
            if not versions:
                raise ValueError("Message unavailable")
            orig = json.loads(versions[-1]["payload"])
            cid = None if m["connection_id"] == self.s.dm else m["connection_id"]
            await self.s.command(
                {"chat": {"id": m["chat_id"]}, "reply_to_message": orig},
                key + (" 3" if key == "q" else ""),
                cid,
                private=True,
            )
            return
        elif section == "api":
            text = "<b>🔑 API</b>\nЧтение только вашего архива через локальный адрес сервера\n\nКлюч: " + (
                "активен" if self.db.meta("api_hash") else "не создан"
            )
            rows = [
                [button("Создать / заменить", "apikey:rotate"), button("Отозвать", "apikey:off")],
                self.ui.nav("plugins"),
            ]
        elif section == "apikey":
            if p[1] not in ("rotate", "off"):
                raise ValueError("Invalid API action")
            await self.s.command({"chat": {"id": self.s.owner}}, "api " + p[1], None)
            await self.show("api", target)
            return
        elif section in ("premium", "plan"):
            if section == "plan":
                if p[1] not in ("free", "demo"):
                    raise ValueError("Unknown plan")
                self.db.set_meta("plan", "demo-premium" if p[1] == "demo" else "free")
            demo = self.db.meta("plan", "free") == "demo-premium"
            text = (
                "<b>◇ Premium · DEMO</b>\n\n<b>Local Free</b> · 0 ₽\nАрхив, все функции и локальный AI\n\n<b>Premium DEMO</b> · 0 ₽\nДемонстрация тарифа; оплата не подключена Никаких списаний\n\nТекущий тариф: "
                + ("Premium DEMO" if demo else "Local Free")
            )
            rows = [
                [
                    button(
                        "Вернуться на Free" if demo else "Включить DEMO", "plan:free" if demo else "plan:demo"
                    )
                ],
                [button("Плагины", "plugins")],
                self.ui.nav("profile"),
            ]
        elif section == "locale":
            text = (
                "<b>🌐 Язык</b>\nСейчас: "
                + self.db.meta("lang", "ru").upper()
                + "\nПодробные подсказки пока на русском"
            )
            rows = [
                [button("Русский", "language:ru"), button("English", "language:en")],
                self.ui.nav("profile"),
            ]
        elif section in ("watchstop", "watchhistory"):
            r = self.db.one(
                "SELECT w.rowid rid,w.* FROM watches w WHERE w.rowid=? AND (w.connection_id=? OR EXISTS(SELECT 1 FROM connections c WHERE c.id=w.connection_id AND c.owner_id=?))",
                (int(p[1]), self.s.dm, self.s.owner),
            )
            if not r:
                raise ValueError("Watch unavailable")
            if section == "watchstop":
                self.db.run("DELETE FROM watches WHERE rowid=?", (r["rid"],))
                await self.watches(target)
            else:
                await self.s.watch(
                    {"chat": {"id": self.s.owner}},
                    str(r["user_id"]),
                    r["connection_id"],
                    "search",
                    lambda value: self.s.send(self.s.owner, value),
                )
            return
        elif section == "accounts":
            key = p[1]
            if key not in ("timename", "timebio"):
                raise ValueError("Unknown account operation")
            text = f"<b>{SPECS[key][1]}</b>\nВыберите свой Business-аккаунт."
            for c in self.db.all(
                "SELECT rowid rid,* FROM connections WHERE owner_id=? AND enabled=1", (self.s.owner,)
            ):
                prof = json.loads(c["payload"]).get("user", {})
                rows += [[button(prof.get("first_name", "Business"), f"clock:{key}:{c['rid']}")]]
            if not rows:
                rows = [[button("Подключение", "connect")]]
            rows += [self.ui.nav("fn:" + key)]
        elif section == "clock":
            from .clocks import toggle

            c = self.db.one(
                "SELECT * FROM connections WHERE rowid=? AND owner_id=? AND enabled=1",
                (int(p[2]), self.s.owner),
            )
            if not c:
                raise ValueError("Account unavailable")
            await toggle(self.s, p[1], c)
            return
        elif section in ("publishstory", "storystatus"):
            from .stories import publish, status

            if section == "publishstory":
                publish(self.s, p[1])
            status(self.s, p[1], target)
            return
        else:
            raise ValueError("Unknown feature route")
        self.ui.card(text, rows, target)

    def pages(self, route, page, count):
        rows = []
        if page:
            rows += [button("‹", f"{route}:{page - 1}")]
        if (page + 1) * 6 < count:
            rows += [button("›", f"{route}:{page + 1}")]
        return [rows] if rows else []

    async def watches(self, target=None):
        items = self.db.all(
            "SELECT w.rowid rid,w.*,u.profile FROM watches w LEFT JOIN users u ON u.connection_id=w.connection_id AND u.id=w.user_id WHERE w.connection_id=? OR EXISTS(SELECT 1 FROM connections c WHERE c.id=w.connection_id AND c.owner_id=?) ORDER BY w.rowid DESC LIMIT 20",
            (self.s.dm, self.s.owner),
        )
        text = "<b>👁 Наблюдаемые</b>\nИзменения фиксируются при новых сообщениях"
        rows = []
        for n, r in enumerate(items, 1):
            prof = json.loads(r["profile"] or "{}")
            text += f"\n\n{n}. {safe(prof.get('first_name', 'Пользователь'), 80)}" + (
                " · @" + safe(prof["username"], 40) if prof.get("username") else ""
            )
            rows += [
                [
                    button(f"{n} · История", f"watchhistory:{r['rid']}"),
                    button("Остановить", f"watchstop:{r['rid']}"),
                ]
            ]
        if not items:
            text += "\n\nПока пусто Выберите чат и включите наблюдение"
        rows += [self.ui.nav("fn:watch_list")]
        self.ui.card(text, rows, target)
