"""
Unit tests for smbs_bridge.get_tooling_library (S.71 C1).
Tests lazy import, default canonical path, custom path, and error handling.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from freecad_smbs.integration.smbs_bridge import get_tooling_library
from smbs_core.geometry_backend.io.tooling_library import (
    ToolingLibrary,
    ToolingLibraryError,
)


class TestToolingBridge(unittest.TestCase):
    def test_get_tooling_library_lazy_imports(self):
        """Importing smbs_bridge top-level must not import FreeCAD or OCC."""
        import subprocess
        import sys

        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.integration.smbs_bridge; "
            "forbidden = ['FreeCAD','FreeCADGui','Part','PySide6','PySide2','PyQt5','PyQt6','OCC','OCP']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded forbidden modules: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_get_tooling_library_default_path(self):
        """get_tooling_library() without args defaults to canonical toollibrary/."""
        lib = get_tooling_library()
        self.assertIsInstance(lib, ToolingLibrary)
        repo_root = Path(__file__).resolve().parents[2]
        expected_path = (repo_root / "toollibrary").resolve()
        self.assertEqual(lib.library_root, expected_path)

    def test_get_tooling_library_custom_path(self):
        """get_tooling_library(path) loads from specified valid directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "punch").mkdir()
            (tmp_path / "die").mkdir()

            lib = get_tooling_library(tmp_path)
            self.assertIsInstance(lib, ToolingLibrary)
            self.assertEqual(lib.library_root, tmp_path.resolve())

    def test_get_tooling_library_raises_on_missing_subdirs(self):
        """get_tooling_library(path) raises ToolingLibraryError if punch/ or die/ missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with self.assertRaises(ToolingLibraryError):
                get_tooling_library(tmp_path)


if __name__ == "__main__":
    unittest.main()
