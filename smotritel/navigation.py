# нижнее меню
MAIN = [
    ["🗂 Архив", "🗑 Удалённые"],
    ["🤖 AI", "🎬 TikTok"],
    ["🌐 Перевод", "🔎 Яндекс"],
    ["🌤 Погода", "🎙 Summary"],
    ["✏️ Изменённые", "💾 Сохранённые"],
    ["⚙️ Настройки", "➕ Ещё"],
]

MORE = [
    ["📁 Медиа", "👤 Профиль"],
    ["🧰 Инструменты", "❓ Помощь"],
    ["📊 Статистика", "🔌 Подключение"],
    ["🏠 Главная"],
]
ROUTES = {
    "🗂 Архив": "a:all:0",
    "🎬 TikTok": "launch:tiktok",
    "🌐 Перевод": "launch:translate",
    "🔎 Яндекс": "launch:yandex",
    "🌤 Погода": "launch:weather",
    "🗑 Удалённые": "a:deleted:0",
    "✏️ Изменённые": "a:edited:0",
    "🤖 AI": "ask",
    "📊 Статистика": "stats",
    "⚙️ Настройки": "settings",
    "🔌 Подключение": "connect",
    "➕ Ещё": "more",
    "🎙 Summary": "voice",
    "💾 Сохранённые": "a:saved:0",
    "📁 Медиа": "media",
    "👤 Профиль": "profile",
    "🧰 Инструменты": "tools",
    "❓ Помощь": "help",
    "🏠 Главная": "menu",
    "❌ Отмена": "cancel",
}
MAIN_EN = [
    ["🗂 Archive", "🗑 Deleted"],
    ["🤖 AI", "🎬 TikTok"],
    ["🌐 Translate", "🔎 Search"],
    ["🌤 Weather", "🎙 Summary"],
    ["✏️ Edited", "💾 Saved"],
    ["⚙️ Settings", "➕ More"],
]
MORE_EN = [
    ["📁 Media", "👤 Profile"],
    ["🧰 Tools", "❓ Help"],
    ["📊 Statistics", "🔌 Connection"],
    ["🏠 Home"],
]
for localized, original in zip(
    [b for r in MAIN_EN + MORE_EN for b in r], [b for r in MAIN + MORE for b in r]
):
    ROUTES[localized] = ROUTES[original]
ROUTES["❌ Cancel"] = "cancel"


def normalize_label(text):

    return "".join(text.replace("\ufe0f", "").replace("\ufe0e", "").split()).casefold()


NORMALIZED_ROUTES = {normalize_label(label): route for label, route in ROUTES.items()}


def route_for_text(text):
    return NORMALIZED_ROUTES.get(normalize_label(text))


def reply_keyboard(lang="ru", waiting=False, more=False, rows=None):
    rows = (
        rows
        if rows is not None
        else [list(r) for r in ((MORE_EN if more else MAIN_EN) if lang == "en" else MORE if more else MAIN)]
    )
    if waiting:
        rows.append(["❌ Cancel", "🏠 Home"] if lang == "en" else ["❌ Отмена", "🏠 Главная"])
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "Choose a section or type a message"
        if lang == "en"
        else "Выберите раздел или напишите сообщение",
    }
