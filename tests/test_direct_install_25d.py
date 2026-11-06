"""Direct installation contract for the canonical 2D FreeCAD addon checkout."""
from __future__ import annotations

import unittest
from pathlib import Path


class DirectInstallTests(unittest.TestCase):
    def test_freecad_exec_entrypoints_without_dunder_file(self):
        """FreeCAD executes Init.py and InitGui.py via exec(code)."""
        root = Path(__file__).resolve().parents[1]
        for name in ("Init.py", "InitGui.py"):
            with self.subTest(entrypoint=name):
                scope = {"__name__": "__freecad_mod_loader__"}
                code = compile((root / name).read_text(encoding="utf-8"),
                               str(root / name), "exec")
                exec(code, scope)
                self.assertEqual(scope["_REPO_ROOT"], root)

    def test_freecad_modcache_lookup_without_search_path(self):
        """FreeCAD may load workbench from Mod registry before sys.path entry."""
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[1]
        for name in ("Init.py", "InitGui.py"):
            with self.subTest(entrypoint=name):
                source = (root / name).read_text(encoding="utf-8")
                prelude = source.split("_REPO_ROOT = _checkout_root()", 1)[0]
                scope = {"__name__": "__freecad_mod_loader__"}
                app = SimpleNamespace(__ModCache__=[SimpleNamespace(path=root)])
                with (
                    patch.dict(sys.modules, {"FreeCAD": app}),
                    patch.object(sys, "path", ["/nonexistent/empty"]),
                ):
                    exec(compile(prelude, str(root / name), "exec"), scope)
                    self.assertEqual(scope["_checkout_root"](), root)

    def test_root_entrypoints_are_available_for_freecad_mod(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("Init.py", "InitGui.py", "freecad_smbs/InitGui.py",
                     "freecad_smbs/commands/pressbrake_2d.py",
                     "freecad_smbs/commands/tooling.py"):
            with self.subTest(path=name):
                self.assertTrue((root / name).is_file(), name)

    def test_only_two_active_commands(self):
        from freecad_smbs.InitGui import COMMANDS_MAP
        self.assertEqual(set(COMMANDS_MAP), {"SMBS_PressBrake2D", "SMBS_Tooling"})
        for cls in COMMANDS_MAP.values():
            self.assertTrue(callable(cls))

    def test_tooling_assets_are_repository_relative(self):
        from freecad_smbs.resources.tooling_paths import (
            REPO_ROOT, TOOL_LIBRARY_ROOT, is_tool_available,
        )
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(REPO_ROOT, root)
        self.assertEqual(TOOL_LIBRARY_ROOT, root / "toollibrary")
        self.assertTrue(is_tool_available("punch"))
        self.assertTrue(is_tool_available("die"))
        self.assertTrue(is_tool_available("backgauge"))
        self.assertTrue(is_tool_available("clamps"))
        self.assertTrue(is_tool_available("machine_frame"))

    def test_tool_library_import_without_old_headless_pipeline(self):
        from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary
        from freecad_smbs.commands.tooling import ToolingCommand
        self.assertTrue(callable(ToolingLibrary))
        self.assertTrue(ToolingCommand().IsActive())

    def test_demo_paths_are_within_checkout(self):
        from smbs_core.demo_paths import DEMO_PARTS_DIR
        self.assertEqual(DEMO_PARTS_DIR, Path(__file__).resolve().parents[1] / "demo" / "parts")


if __name__ == "__main__":
    unittest.main()
