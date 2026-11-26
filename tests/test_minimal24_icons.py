"""Tests for FreeCAD-independent SVG resources of the minimal24 icon theme."""
import importlib.util
from pathlib import Path
from xml.etree import ElementTree as ET
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "freecad/bendseq" / "icon_theme.py"
spec = importlib.util.spec_from_file_location("icon_theme_minimal24", MOD)
theme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(theme)


class TestMinimal24Icons(unittest.TestCase):
    def test_assets_and_alpha_background(self):
        light = theme._MINIMAL / "light"
        dark = theme._MINIMAL / "dark"
        names = {p.name for p in light.glob("*.svg")}
        self.assertEqual(len(names), 24)
        self.assertEqual(names, {p.name for p in dark.glob("*.svg")})
        for root in (light, dark):
            for name in names:
                path = root / name
                with self.subTest(path=str(path)):
                    elt = ET.parse(path).getroot()
                    self.assertEqual(elt.tag, "{http://www.w3.org/2000/svg}svg")
                    self.assertEqual(elt.get("viewBox"), "0 0 24 24")
                    self.assertEqual(elt.get("fill"), "none")

    def test_unknown_icon_fallback(self):
        self.assertEqual(theme.icon_path("gui_backgauge.svg"),
                         theme._ORIGINAL / "gui_backgauge.svg")


if __name__ == "__main__":
    unittest.main()
