"""Source-level GUI boundary tests; executable without FreeCAD or Qt.

These tests protect architectural contracts while live GUI behavior remains
covered by tests/test_pressbrake_workspace_gui.py in FreeCAD.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "freecad_smbs" / "commands" / "pressbrake_2d.py"
PANEL = ROOT / "freecad_smbs" / "ui" / "pressbrake_panel.py"
SESSION = ROOT / "freecad_smbs" / "integration" / "part_session.py"
COMMON = ROOT / "freecad_smbs" / "ui" / "pressbrake_common.py"


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def classes(tree):
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def functions(tree):
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


class GuiModuleBoundaryTests(unittest.TestCase):
    def test_all_extracted_modules_parse(self):
        for path in (COMMAND, PANEL, SESSION, COMMON):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIsInstance(parse(path), ast.Module)

    def test_workbench_command_owns_no_window_or_session_classes(self):
        tree = parse(COMMAND)
        self.assertIn("PressBrake2DCommand", classes(tree))
        self.assertNotIn("_PreviewPanel", classes(tree))
        self.assertNotIn("LoadedPartSession", classes(tree))

    def test_session_has_no_qt_or_command_imports(self):
        tree = parse(SESSION)
        self.assertIn("LoadedPartSession", classes(tree))
        self.assertTrue({"build_part_session", "execute_load_part", "resolve_selected_unfold"} <= functions(tree))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        self.assertFalse(any("PySide" in name or name.startswith("FreeCADGui")
                             or name.startswith("freecad_smbs.commands")
                             for name in imports))

    def test_panel_is_only_workspace_owner(self):
        self.assertIn("_PreviewPanel", classes(parse(PANEL)))
        self.assertNotIn("PressBrake2DCommand", classes(parse(PANEL)))

    def test_backward_compatible_command_exports(self):
        tree = parse(COMMAND)
        exports = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                exports.setdefault(node.module or "", set()).update(alias.name for alias in node.names)
        self.assertIn("_PreviewPanel", exports.get("freecad_smbs.ui.pressbrake_panel", set()))
        self.assertIn("LoadedPartSession", exports.get("freecad_smbs.integration.part_session", set()))


if __name__ == "__main__":
    unittest.main()
