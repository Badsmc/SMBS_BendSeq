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


@lru_cache(maxsize=1)
def _source_keys():
    """Resolve English and legacy Russian text without a duplicate catalog."""
    keys = {}
    for language in ("en", "ru"):
        for key, message in _catalog(language).items():
            keys.setdefault(message, key)
    return keys


def _translation_key(source):
    if source in _catalog("en"):
        return source
    return _source_keys().get(source, source)


@lru_cache(maxsize=4)
def language_metadata(language):
    path = Path(__file__).resolve().parents[1] / "translations" / language / "metadata.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def resolve_language(freecad_language="", system_language="en"):
    language = freecad_language.strip().casefold()
    if language in ("", "system", "system default", "auto", "default"):
        language = system_language.strip().casefold()
    token = language.replace("-", "_").replace(" ", "_").split("_", 1)[0]
    if token in language_metadata("ru").get("locale_names", ()):
        return "ru"
    return {
        "ru": "ru", "russian": "ru",
        "en": "en", "english": "en",
    }.get(token, "en")


def interface_language():
    try:
        import FreeCAD as App
    except ImportError:
        return "en"
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    preference = general.GetString("Language", "")
    try:
        from PySide import QtCore
    except ImportError:
        QtCore = None
    system_language = QtCore.QLocale().name() if QtCore is not None else (locale.getlocale()[0] or "en")
    return resolve_language(preference, system_language)


def tr(source, *, language=None, **values):
    native = language is None
    language = interface_language() if native else language
    key = _translation_key(source)
    english = _catalog("en").get(key, source)
    source = _catalog(language).get(key, english)
    # Keep headless and explicit-language calls deterministic. Prefer the
    # native Qt translator when FreeCAD has loaded the matching QM resource.
    if native and language == "ru":
        try:
            from PySide import QtCore
            translated = QtCore.QCoreApplication.translate("BendSeq", english, key)
            if translated != english:
                source = translated
        except (ImportError, AttributeError):
            pass
    if values:
        diagnostic_fields = {"error", "reason", "details", "warning", "message", "text"}
        values = {
            key: tr_message(value, language=language)
            if key in diagnostic_fields and isinstance(value, (str, Exception)) else value
            for key, value in values.items()
        }
    return source.format(**values) if values else source


@lru_cache(maxsize=4)
def _message_patterns(source_language, target_language):
    """Match already formatted diagnostics from the headless core."""
    patterns = []
    formatter = Formatter()
    for key, source in _catalog(source_language).items():
        translated = _catalog(target_language).get(key, source)
        if "{" not in source or source == translated:
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
    if not value:
        return value
    language = interface_language() if language is None else language
    if language not in ("en", "ru"):
        language = "en"
    if value.startswith("UNVERIFIED: "):
        return "UNVERIFIED: " + tr_message(value[len("UNVERIFIED: "):], language=language)
    exact = _catalog(language).get(_translation_key(value))
    if exact is not None:
        return exact
    source_language = "ru" if language == "en" else "en"
    for _, pattern, chunks, translated in _message_patterns(source_language, language):
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
