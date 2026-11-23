"""Shared SMBS interface language and source-string translation catalog."""

import locale


TRANSLATIONS = {
    "FAST — варианты последовательности (UNVERIFIED)": "FAST — candidate sequences (unverified)",
    "Проверить ещё раз / найти новые варианты": "Check again / find more sequences",
    "Вес": "Cost",
    "2D гибов": "2D bends",
    "Старт": "Start",
    "1-й гиб": "First bend",
    "Ось": "Axis",
    "2D штраф": "2D penalty",
    "Порядок": "Sequence",
    "Причина": "Reason",
    "Ошибка": "Error",
    "Предупреждение": "Warning",
    "Панель задач": "Tasks",
    "Деталь": "Part",
    "Оснастка": "Tooling",
    "Симуляция": "Simulation",
    "Настройки": "Settings",
    "Готово": "Ready",
    "Расчёт…": "Working…",
    "Информация": "Information",
    "Закрыть раздел": "Close section",
    "Показать панель управления": "Show controls",
    "Выбрать линии гиба": "Select bend lines",
    "Отменить линию": "Undo line",
    "Очистить": "Clear",
    "Сохранённые варианты": "Saved sequences",
    "Симулировать без проверки": "Simulate without checking",
    "Быстрая проверка OCC (5 углов)": "Quick OCC check (5 angles)",
    "Проверить и показать": "Check and show",
    "Показать коллизию в 3D": "Show collision in 3D",
    "FAST · найти последовательности (до 30 с)": "FAST · Find sequences (up to 30 s)",
    "Mesh 3D (60 с)": "Mesh 3D (60 s)",
    "Коллизии Mesh": "Mesh collisions",
    "Импортировать…": "Import…",
    "Дополнительно…": "Advanced…",
    "Сохранить в FCStd": "Save to FCStd",
    "Восстановить из FCStd": "Restore from FCStd",
    "Записывать журнал SMBS и сбои FreeCAD": "Log SMBS activity and FreeCAD crashes",
    "Не удалось создать журнал на рабочем столе.": "Could not create the Desktop log.",
    "Проверка ещё не запускалась.": "Check has not run yet.",
    "Поиск ещё не запускался.": "Search has not run yet.",
    "Идёт поиск последовательности…": "Searching for a sequence…",
    "Поиск не выполнен.": "Search failed.",
    "Поиск завершён. Варианты не найдены.": "Search finished. No sequences found.",
    "Выберите вариант в окне FAST.": "Select a sequence in the FAST window.",
    "2D-проверка вариантов не выполнена.": "The 2D check failed.",
    "Порядок гибов применён.": "Bend sequence applied.",
    "Вариант не применён.": "Sequence could not be applied.",
    "Последовательность: {order}": "Sequence: {order}",
    "Поиск ещё не запускался для этой детали.": "Search has not run for this part.",
    "Проверка ещё не запускалась для этой детали.": "This part has not been checked yet.",
    "Проверка коллизий…": "Checking for collisions…",
    "Проверка завершена не полностью.": "The check is incomplete.",
    "В проверенных положениях коллизий не обнаружено.": "No collisions found at the checked positions.",
    "Ошибка проверки.": "Check failed.",
    "Коллизия": "Collision",
    "Торцевое пересечение": "End-zone overlap",
    "Не удалось уточнить": "Could not verify",
    "пуансон": "punch",
    "матрица": "die",
    "Найдено вариантов: {count}. Шаблонов: {templates}.": "Sequences found: {count}. Templates: {templates}.",
    "Поиск занял {seconds:.2f} с. 3D-коллизии проверяются отдельно через Mesh.": "Search took {seconds:.2f} s. Use Mesh to check for 3D collisions.",
    "Предварительная 2D-проверка завершена. Вариантов: {count}.": "Preliminary 2D check finished. Sequences: {count}.",
    "Без замечаний в 2D: {clear}\nС препятствиями: {blocked}\nБез вывода: {unknown}": "No issues found in 2D: {clear}\nObstructions: {blocked}\nInconclusive: {unknown}",
    "Подтверждённых коллизий: {count}.": "Confirmed collisions: {count}.",
    "Условных пересечений на торцах: {count}.": "Conditional end-zone overlaps: {count}.",
    "Проверено гибов: {checked}/{total}. Время: {seconds:.1f} с.": "Bends checked: {checked}/{total}. Time: {seconds:.1f} s.",
    "Осталось проверить: {order}": "Remaining: {order}",
    "Достигнут лимит времени.": "Time limit reached.",
    "Деталь, порядок или оснастка изменились.": "Part, sequence or tooling has changed.",
    "Запустите Mesh заново для текущей конфигурации.": "Run Mesh again for the current setup.",
}


def resolve_language(freecad_language="", system_language="en"):
    if freecad_language.strip().lower() in ("", "system", "auto", "default"):
        freecad_language = system_language
    token = freecad_language.strip().lower().replace("-", "_").split("_", 1)[0]
    return {
        "ru": "ru", "russian": "ru", "русский": "ru",
        "en": "en", "english": "en",
    }.get(token, "en")


def interface_language():
    try:
        import FreeCAD as App
    except ImportError:
        return "ru"
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    return resolve_language(general.GetString("Language", ""), locale.getlocale()[0] or "en")


def tr(source, *, language=None, **values):
    language = interface_language() if language is None else language
    translated = TRANSLATIONS.get(source)
    if translated and language == "en":
        source = translated
    return source.format(**values) if values else source
