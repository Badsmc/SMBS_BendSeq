"""Regression for FreeCAD execution of addon root entrypoints without __file__."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RootEntrypointTests(unittest.TestCase):
    def test_exec_entrypoints_without_file_global(self):
        # FreeCAD executes InitGui.py/Init.py as a script and may not provide
        # __file__. Avoid importing the actual FreeCAD GUI for this test.
        import importlib.util
        for path, module_name in (
            ("Init.py", "freecad_smbs.Init"),
            ("InitGui.py", "freecad_smbs.InitGui"),
        ):
            with self.subTest(path=path):
                package = types.ModuleType("freecad_smbs")
                package.__path__ = [str(ROOT / "freecad_smbs")]
                submodule = types.ModuleType(module_name)
                submodule.MARKER = path
                saved = {key: sys.modules.get(key) for key in
                         ("freecad_smbs", module_name)}
                try:
                    sys.modules["freecad_smbs"] = package
                    sys.modules[module_name] = submodule
                    with patch.object(importlib.util, "find_spec", return_value=object()):
                        ns = {"__name__": "__freecad_exec__"}
                        exec(compile((ROOT / path).read_text(encoding="utf-8"),
                                     path, "exec"), ns)
                        self.assertEqual(ns["MARKER"], path)
                finally:
                    for key, value in saved.items():
                        if value is None:
                            sys.modules.pop(key, None)
                        else:
                            sys.modules[key] = value


if __name__ == "__main__":
    unittest.main()
