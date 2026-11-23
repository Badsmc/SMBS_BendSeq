"""Ensure the selected FreeCAD icon theme is complete and self-contained."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ICON_MODULE = ROOT / "freecad_smbs" / "icon_pack.py"
SPEC = importlib.util.spec_from_file_location("smbs_icon_pack_test", ICON_MODULE)
ICONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ICONS)


class IconPackTests(unittest.TestCase):
    def test_selected_icons_are_valid_svg(self):
        for name in sorted(ICONS._NAMES):
            with self.subTest(name=name):
                path = ICONS.icon_path(name + ".svg")
                self.assertTrue(path.is_file(), path)
                root = ET.parse(path).getroot()
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertEqual(root.get("viewBox"), "0 0 64 64")

    def test_unknown_icons_keep_legacy_fallback(self):
        self.assertEqual(
            ICONS.icon_path("gui_backgauge.svg"),
            ICONS._ORIGINAL / "gui_backgauge.svg",
        )


if __name__ == "__main__":
    unittest.main()
