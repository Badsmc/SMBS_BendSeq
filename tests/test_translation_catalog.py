"""Guard the shipped English catalog against missing UI strings and format errors."""
import ast
import json
from pathlib import Path
import re
import string
import unittest


ROOT = Path(__file__).resolve().parents[1] / "freecad_smbs"
CORE = ROOT.parent / "smbs_core"
RUSSIAN = re.compile(r"[А-Яа-яЁё]")


class TranslationCatalogTest(unittest.TestCase):
    def test_core_diagnostic_fragments_are_accounted_for(self):
        catalog = json.loads((ROOT / "translations/en/catalog.json").read_text(encoding="utf-8"))
        # These pieces are assembled into formatted catalog templates or are
        # internal identifiers; a new literal must be translated explicitly.
        fragments = {
            "Нет проверенного FCStd для ", "Непригодная сетка оснастки: ",
            "Пустая сетка панели ", "Шаблон", "Этап ",
            "Неизвестный физический гиб: ", "Догиб ",
            " нельзя ставить раньше предварительного гиба.",
            "Неизвестный гиб «", "». Используйте номера с развёртки.",
            "Гиб ", " указан несколько раз.",
        }
        missing = []
        for source_file in CORE.rglob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and RUSSIAN.search(node.value)
                        and node.value not in catalog and node.value not in json.loads((ROOT / "translations/source_keys.json").read_text()) and node.value not in fragments):
                    missing.append(f"{source_file.relative_to(CORE)}:{node.lineno}: {node.value[:90]}")
        self.assertEqual(missing, [])

    def test_russian_ui_literals_have_catalog_entries(self):
        catalog = json.loads((ROOT / "translations/en/catalog.json").read_text(encoding="utf-8"))
        # These are input tokens used to recognize locale or status, not UI output.
        internal = {
            "русский", "стоп:", "предупрежден", "предупреждён",
            "поиск последовательности (глубина", r"[А-Яа-яЁё]",
        }
        missing = []
        for source_file in ROOT.rglob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and RUSSIAN.search(node.value)
                        and node.value not in catalog and node.value not in json.loads((ROOT / "translations/source_keys.json").read_text()) and node.value not in internal):
                    missing.append(f"{source_file.relative_to(ROOT)}:{node.lineno}: {node.value[:90]}")
        self.assertEqual(missing, [])

    def test_freecad_language_preference_selects_catalog(self):
        from freecad_smbs.ui.localization import resolve_language, tr

        self.assertEqual(resolve_language("English", "ru_RU"), "en")
        self.assertEqual(resolve_language("Русский", "en_US"), "ru")
        self.assertEqual(resolve_language("", "ru_RU"), "ru")
        self.assertEqual(tr("Симуляция остановлена. {reason}", language="en", reason="B01"),
                         "Simulation stopped. B01")
        self.assertEqual(tr("Симуляция остановлена. {reason}", language="ru", reason="B01"),
                         "Симуляция остановлена. B01")

    def test_formatted_core_errors_are_translated_at_ui_boundary(self):
        from freecad_smbs.ui.localization import tr_message
        from smbs_core.kinematics2d.fold_sequence import parse_manual_bend_order

        self.assertEqual(tr_message("Гиб B02 указан несколько раз.", language="en"),
                         "Bend B02 is listed more than once.")
        self.assertEqual(tr_message("Некорректная длина последовательности: ожидалось 10, получено 9.", language="en"),
                         "Incorrect sequence length: expected 10, got 9.")
        self.assertEqual(tr_message("Гиб B02 указан несколько раз.", language="ru"),
                         "Гиб B02 указан несколько раз.")
        with self.assertRaises(ValueError) as raised:
            parse_manual_bend_order("B01 B01", ("B01", "B02"))
        self.assertEqual(tr_message(raised.exception, language="en"),
                         "Bend B01 is listed more than once.")
        self.assertEqual(tr_message("UNVERIFIED: Гиб B01 указан несколько раз.", language="en"),
                         "UNVERIFIED: Bend B01 is listed more than once.")
        from freecad_smbs.ui.localization import tr
        self.assertEqual(tr("Ошибка загрузки детали: {error}", language="en", error=raised.exception),
                         "Could not load the part: Bend B01 is listed more than once.")
        self.assertEqual(tr("Пуансон: {punch} | Матрица: {die} | Геометрия: {geometry} | Коллизии: НЕ ПРОВЕРЕНЫ",
                            language="en", punch="Шаблон", die="D1", geometry="verified").split(" | ")[0],
                         "Punch: Шаблон")
        self.assertEqual(tr_message("Непригодная сетка оснастки: DIE", language="en"),
                         "Invalid tooling mesh: DIE")

    def test_literal_tr_calls_are_translated(self):
        catalog = json.loads((ROOT / "translations/en/catalog.json").read_text(encoding="utf-8"))
        missing = []
        for source_file in ROOT.rglob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "tr":
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    continue
                source = node.args[0].value
                if RUSSIAN.search(source) and source not in catalog:
                    missing.append(f"{source_file.relative_to(ROOT)}:{node.lineno}: {source[:90]}")
        self.assertEqual(missing, [])

    def test_catalog_format_fields_match_sources(self):
        catalog = json.loads((ROOT / "translations/en/catalog.json").read_text(encoding="utf-8"))
        formatter = string.Formatter()
        russian = json.loads((ROOT / "translations/ru/catalog.json").read_text(encoding="utf-8"))
        for key, translation in catalog.items():
            source = russian[key]
            with self.subTest(source=source[:90]):
                self.assertFalse(RUSSIAN.search(translation))
                source_fields = sorted(field for _, field, _, _ in formatter.parse(source) if field is not None)
                translated_fields = sorted(field for _, field, _, _ in formatter.parse(translation) if field is not None)
                self.assertEqual(source_fields, translated_fields)
