# кнопки снизу
import html
import json
from contextlib import nullcontext
from .navigation import MAIN, ROUTES, normalize_label, reply_keyboard
from .db import dump


class Lower:
    def __init__(self, service):
        self.s, self.db = service, service.db

    def revision(self):
        return int(self.db.meta("lower_revision", "0"))

    def set(self, rows, screen="menu"):

        # старый ответ не меняет кнопки
        want = getattr(self.s, "nav_rev", None)
        if self.s.job_mode and want is not None and want != self.revision():
            return self.current()
        routes, keyboard = {}, []
        for row in rows:
            labels = []
            for b in row:
                if "callback_data" not in b:
                    continue
                label = html.unescape(b["text"]).strip()[:60]
                base, n = label, 2
                while normalize_label(label) in routes:
                    label = f"{base[:53]} · {n}"
                    n += 1
                routes[normalize_label(label)] = b["callback_data"]
                labels.append(label)
            if labels:
                keyboard.append(labels)
        if not keyboard:
            return self.main()
        markup = reply_keyboard(self.db.meta("lang", "ru"), rows=keyboard)
        with nullcontext() if self.s.base.conn.in_transaction else self.db.transaction():
            self.db.set_meta("lower_routes", dump(routes))
            self.db.set_meta("lower_markup", dump(markup))
            self.db.set_meta("lower_screen", screen)
            self.db.set_meta("lower_revision", self.revision() + 1)
            self.db.set_meta("keyboard_layout", "main" if screen == "menu" else screen)
            self.db.set_meta("keyboard_boot", self.db.meta("started_at", "0"))
        return markup

    def main(self):
        markup = reply_keyboard(self.db.meta("lang", "ru"))
        rows = [
            [{"text": label, "callback_data": ROUTES[label]} for label in row] for row in markup["keyboard"]
        ]
        return self.set(rows, "menu")

    def more(self):
        from .features import SPECS

        pairs = [
            ("📊 Статистика", "stats"),
            ("🔌 Подключение", "connect"),
            ("👤 Профиль", "profile"),
            ("📁 Медиа", "media"),
        ]
        main_routes = {ROUTES[b] for row in MAIN for b in row}
        pairs += [
            (spec[1], "launch:" + key)
            for key, spec in SPECS.items()
            if "launch:" + key not in main_routes and key not in ("ai", "translate", "tiktok", "weather")
        ]
        pairs += [
            ("🧩 Все функции", "catalog"),
            ("🧰 Инструменты", "tools"),
            ("Плагины", "plugins"),
            ("Premium · DEMO", "premium"),
            ("🔑 API", "api"),
            ("🌐 Язык", "locale"),
            ("🔐 Приватность", "privacy"),
            ("❓ Помощь", "help"),
        ]
        rows = [
            [{"text": label, "callback_data": route} for label, route in pairs[i : i + 2]]
            for i in range(0, len(pairs), 2)
        ]
        rows += [[{"text": "🏠 Главная", "callback_data": "menu"}]]
        return self.set(rows, "more")

    def waiting(self, back="menu"):
        rows = [
            [{"text": label, "callback_data": ROUTES[label]} for label in row]
            for row in reply_keyboard(self.db.meta("lang", "ru"))["keyboard"]
        ]
        rows.append(
            [
                {"text": "❌ Отмена", "callback_data": "cancel"},
                {"text": "🏠 Главная", "callback_data": "menu"},
            ]
        )
        if back != "menu":
            rows.append([{"text": "‹ Назад", "callback_data": back}])
        return self.set(rows, "waiting")

    def current(self):
        value = self.db.meta("lower_markup")
        return json.loads(value) if value else reply_keyboard(self.db.meta("lang", "ru"))

    def route(self, text):
        routes = json.loads(self.db.meta("lower_routes", "{}") or "{}")
        return routes.get(normalize_label(text))
