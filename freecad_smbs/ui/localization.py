"""Shared SMBS interface language and source-string translation catalog."""

import locale
from functools import lru_cache
from pathlib import Path
import json
import re
from string import Formatter


@lru_cache(maxsize=2)
def _catalog(language):
    path = Path(__file__).resolve().parents[1] / "translations" / language / "catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"Invalid translation catalog: {path}")
    return data


SOURCE_KEYS = json.loads(
    (Path(__file__).resolve().parents[1] / "translations" / "source_keys.json").read_text(encoding="utf-8")
)


def resolve_language(freecad_language="", system_language="en"):
    language = freecad_language.strip().casefold()
    if language in ("", "system", "system default", "auto", "default"):
        language = system_language.strip().casefold()
    token = language.replace("-", "_").replace(" ", "_").split("_", 1)[0]
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
    preference = general.GetString("Language", "")
    try:
        from PySide import QtCore
    except ImportError:
        try:
            from PySide6 import QtCore
        except ImportError:
            try:
                from PySide2 import QtCore
            except ImportError:
                QtCore = None
    system_language = QtCore.QLocale().name() if QtCore is not None else (locale.getlocale()[0] or "en")
    return resolve_language(preference, system_language)


def tr(source, *, language=None, **values):
    language = interface_language() if language is None else language
    key = SOURCE_KEYS.get(source, source)
    source = _catalog(language).get(key, _catalog("en").get(key, source))
    if language == "en" and values:
        diagnostic_fields = {"error", "reason", "details", "warning", "message", "text"}
        values = {
            key: tr_message(value, language=language)
            if key in diagnostic_fields and isinstance(value, (str, Exception)) else value
            for key, value in values.items()
        }
    return source.format(**values) if values else source


@lru_cache(maxsize=1)
def _message_patterns():
    """Match already formatted diagnostics from the headless core."""
    patterns = []
    formatter = Formatter()
    for source, key in SOURCE_KEYS.items():
        translated = _catalog("en")[key]
        if "{" not in source or not re.search(r"[А-Яа-яЁё]", source):
            continue
        try:
            chunks = list(formatter.parse(source))
        except ValueError:
            continue
        literal_length = sum(len(literal) for literal, _, _, _ in chunks)
        if literal_length < 12:
            continue
        expression = "^" + "".join(
            re.escape(literal) + ("(.+?)" if field is not None else "")
            for literal, field, _, _ in chunks
        ) + "$"
        patterns.append((literal_length, re.compile(expression, re.DOTALL), chunks, translated))
    return tuple(sorted(patterns, key=lambda item: item[0], reverse=True))


def tr_message(message, *, language=None):
    """Translate a fully formatted core error without making core depend on FreeCAD."""
    value = str(message)
    language = interface_language() if language is None else language
    if language != "en" or not re.search(r"[А-Яа-яЁё]", value):
        return value
    if value.startswith("UNVERIFIED: "):
        return "UNVERIFIED: " + tr_message(value[len("UNVERIFIED: "):], language=language)
    exact = _catalog("en").get(SOURCE_KEYS.get(value, value))
    if exact is not None:
        return exact
    for _, pattern, chunks, translated in _message_patterns():
        match = pattern.fullmatch(value)
        if match is None:
            continue
        captured = iter(match.groups())
        by_name = {}
        positions = []
        for _, field, _, _ in chunks:
            if field is not None:
                part = next(captured)
                positions.append(part)
                if field and not field.isdigit():
                    by_name[field] = part
        index = 0
        output = []
        for literal, field, _, _ in Formatter().parse(translated):
            output.append(literal)
            if field is not None:
                if field and not field.isdigit():
                    output.append(by_name[field])
                elif field:
                    output.append(positions[int(field)])
                else:
                    output.append(positions[index])
                    index += 1
        return "".join(output)
    return value
