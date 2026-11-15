"""Regression guard for Python syntax in FreeCAD GUI entrypoints.

FreeCAD loads InitGui by exec(), so a SyntaxError in a lazily imported
command module prevents the entire workbench from registering.
This test runs without FreeCAD, PySide or a graphical display.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

GUI_FILES = (
    "InitGui.py",
    "Init.py",
    "freecad_smbs/InitGui.py",
    "freecad_smbs/commands/pressbrake_2d.py",
)


class TestGuiSourceSyntax(unittest.TestCase):
    def test_entrypoints_and_simulation_module_parse(self):
        for relative in GUI_FILES:
            with self.subTest(path=relative):
                source_path = ROOT / relative
                ast.parse(source_path.read_text(encoding="utf-8"),
                          filename=str(source_path))


if __name__ == "__main__":
    unittest.main()
