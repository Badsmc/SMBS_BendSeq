"""
test_freecad_bootstrap.py - Unit tests for centralized FreeCAD/SheetMetal bootstrap module.
"""
import os
import sys
import unittest
from unittest.mock import patch

from smbs_core.geometry_backend.freecad_bootstrap import (
    get_freecad_lib_candidates,
    get_sheetmetal_mod_candidates,
    ensure_freecad_paths,
)


class TestFreeCADBootstrap(unittest.TestCase):
    def test_get_freecad_lib_candidates_contains_standard_paths(self):
        candidates = get_freecad_lib_candidates()
        self.assertIsInstance(candidates, list)
        self.assertIn("/Applications/FreeCAD.app/Contents/Resources/lib", candidates)
        self.assertIn("/usr/lib/freecad/lib", candidates)

    def test_get_freecad_lib_candidates_respects_env_var(self):
        with patch.dict(os.environ, {"FREECAD_LIB": "/custom/freecad/path"}):
            candidates = get_freecad_lib_candidates()
            self.assertEqual(candidates[0], "/custom/freecad/path")

    def test_get_sheetmetal_mod_candidates_respects_env_var(self):
        with patch.dict(os.environ, {"FREECAD_MOD_SHEETMETAL": "/custom/sheetmetal/mod"}):
            candidates = get_sheetmetal_mod_candidates()
            self.assertEqual(candidates[0], "/custom/sheetmetal/mod")

    def test_ensure_freecad_paths_injects_existing_dir(self):
        with patch("os.path.isdir", side_effect=lambda p: p == "/mock/existing/lib"):
            with patch("smbs_core.geometry_backend.freecad_bootstrap.get_freecad_lib_candidates", return_value=["/mock/existing/lib"]):
                res = ensure_freecad_paths()
                self.assertTrue(res)
                self.assertIn("/mock/existing/lib", sys.path)
                # Cleanup
                sys.path.remove("/mock/existing/lib")


if __name__ == "__main__":
    unittest.main()
