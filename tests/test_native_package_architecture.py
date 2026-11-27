"""Native namespace, headless isolation and registration contracts."""
import ast
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeArchitectureTest(unittest.TestCase):
    def test_no_old_packages_or_runtime_path_mutations(self):
        self.assertFalse((ROOT / "freecad_smbs").exists())
        self.assertFalse((ROOT / "smbs_core").exists())
        self.assertFalse((ROOT / "freecad/__init__.py").exists())
        for file in (ROOT / "freecad/bendseq").rglob("*.py"):
            source = file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                             else [node.module or ""])
                    self.assertFalse(any(n.startswith(("freecad_smbs", "smbs_core")) for n in names), str(file))
                if isinstance(node, ast.Call):
                    self.assertFalse(ast.unparse(node.func).startswith("sys.path."), str(file))

    def test_all_core_modules_import_without_gui_or_qt(self):
        code = '''
import importlib, pathlib, sys
for file in pathlib.Path("freecad/bendseq/core").rglob("*.py"):
    name = ".".join(file.with_suffix("").parts)
    if name.endswith(".__init__"):
        name = name[:-9]
    importlib.import_module(name)
assert not any(n.startswith(("FreeCADGui", "PySide", "PyQt", "pivy")) for n in sys.modules)
assert not any(n.startswith(("freecad_smbs", "smbs_core")) for n in sys.modules)
'''
        subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, check=True)

    def test_gui_entrypoints_register_once_and_keep_command_ids(self):
        code = '''
import importlib, sys, types
from unittest.mock import patch
import freecad.bendseq
import freecad.bendseq.ui.mesh_preferences as mesh
seen = []
commands = {}
gui = types.ModuleType("FreeCADGui")
gui.Workbench = object
gui.listWorkbenches = lambda: {"SMBSWorkbench": True} if seen else {}
gui.addWorkbench = lambda bench: seen.append(type(bench).__name__)
gui.addIconPath = lambda path: None
gui.addLanguagePath = lambda path: None
gui.updateLocale = lambda: None
gui.addPreferencePage = lambda *args: None
gui.listCommands = lambda: list(commands)
gui.addCommand = lambda key, cmd: commands.setdefault(key, cmd)
sys.modules["FreeCADGui"] = gui
with patch.object(freecad.bendseq, "enabled", return_value=True), patch.object(mesh, "migrate_mesh_preferences"):
    exec(open("InitGui.py").read(), {})
    modern = importlib.import_module("freecad.bendseq.init_gui")
    importlib.reload(modern)
assert seen == ["SMBSWorkbench"], seen
wb = modern.workbench
with patch.object(wb, "NativePreferences") as prefs, patch("freecad.bendseq.commands.pressbrake_2d._qt", return_value=(None, None, None)), patch("freecad.bendseq.core.kinematics2d.developer_debug_log.set_debug_log_enabled"):
    wb.register_commands()
    wb.register_commands()
assert len(commands) == 16, commands.keys()
assert "SMBS_FastSearch" in commands and "SMBS_BendInstructionsPdf" in commands
assert not any(n.startswith(("freecad_smbs", "smbs_core")) for n in sys.modules)
'''
        subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, check=True)
