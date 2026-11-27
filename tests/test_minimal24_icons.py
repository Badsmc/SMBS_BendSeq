"""Headless contracts for the independently replaceable BendSeq SVGs."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET
import sys
import unittest

from freecad.bendseq import icon_theme as theme

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "freecad" / "bendseq"
SVG = "{http://www.w3.org/2000/svg}"


class TestMinimal24Icons(unittest.TestCase):
    def test_assets_and_transparent_background(self):
        light = theme._MINIMAL / "light"
        dark = theme._MINIMAL / "dark"
        names = {p.name for p in light.glob("*.svg")}
        self.assertGreaterEqual(len(names), 191)
        self.assertEqual(names, {p.name for p in dark.glob("*.svg")})
        for folder in (light, dark):
            for name in names:
                with self.subTest(theme=folder.name, icon=name):
                    elt = ET.parse(folder / name).getroot()
                    self.assertEqual(elt.tag, SVG + "svg")
                    self.assertEqual(elt.get("viewBox"), "0 0 24 24")
                    paths = elt.findall(SVG + "path")
                    self.assertTrue(paths)
                    self.assertTrue(all(p.get("fill") == "none" for p in paths))
                    self.assertTrue(all(p.get("d") for p in paths))
                    self.assertFalse(elt.findall(SVG + "rect"))
                    self.assertFalse(elt.findall(SVG + "image"))
        for name in ("gui_workbench.svg", "preferences-bendseq.svg"):
            self.assertEqual(ET.parse(theme._ORIGINAL / name).getroot().tag, SVG+"svg")

    def test_theme_resolution_and_original_fallback(self):
        for dark in (False, True):
            with patch.object(theme, "is_dark_theme", return_value=dark):
                name = "btn_fast.svg"
                self.assertEqual(theme.icon_path(name),
                    theme._MINIMAL / ("dark" if dark else "light") / name)
                self.assertEqual(theme.icon_path("gui_workbench.svg"),
                                 theme._ORIGINAL / "gui_workbench.svg")
        self.assertEqual(theme.icon_path("not-provided.svg"),
                         theme._ORIGINAL / "not-provided.svg")

    def test_all_referenced_independent_icons_have_both_variants(self):
        names = set()
        for file in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    value = node.value
                    if value.startswith("btn_") and value.endswith(".svg"):
                        names.add(value)
        self.assertGreaterEqual(len(names), 80)
        for name in names:
            for appearance in ("light","dark"):
                self.assertTrue((theme._MINIMAL / appearance / name).is_file(), name)
        for prefix in ("fast", "mesh"):
            for state in ("running", "done", "warning", "error"):
                for appearance in ("light", "dark"):
                    self.assertTrue((theme._MINIMAL / appearance /
                                    f"status_{prefix}_{state}.svg").is_file())
        self.assertTrue({"btn_fast.svg", "btn_simulation.svg",
                         "btn_bend_plan_pdf.svg", "btn_mesh.svg"}.issubset(names))

    def test_binding_does_not_change_action_handlers(self):
        button = Mock()
        qt = SimpleNamespace(QtCore=SimpleNamespace(Qt=SimpleNamespace(ToolButtonIconOnly=7)),
                             QtGui=SimpleNamespace(QIcon=lambda p: p))
        with patch.dict(sys.modules, {"PySide": qt}), patch.object(theme, "is_dark_theme", return_value=False):
            self.assertIs(theme.set_action_icon(button, "btn_play.svg", compact=True), button)
        button.setIcon.assert_called_once_with(str(theme._MINIMAL / "light" / "btn_play.svg"))
        button.setToolButtonStyle.assert_called_once_with(7)
        button.setText.assert_not_called()
        button.setEnabled.assert_not_called()
        button.clicked.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
