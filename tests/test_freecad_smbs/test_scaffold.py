"""
Unit tests for freecad_smbs scaffold (S.69 Commit C1).
Tests headless safety and integrity of smbs_core.viewer CLI.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class TestFreeCADSMBsScaffold(unittest.TestCase):
    def test_init_headless_import(self):
        """Verify freecad_smbs.Init imports safely in headless environment."""
        import freecad_smbs
        import freecad_smbs.Init

        self.assertTrue(hasattr(freecad_smbs, "__version__"))
        self.assertTrue(hasattr(freecad_smbs.Init, "SMBSWorkbenchHeadless"))
        wb = freecad_smbs.Init.SMBSWorkbenchHeadless()
        self.assertEqual(wb.MenuText, "SMBS")
        self.assertEqual(wb.GetClassName(), "Gui::PythonWorkbench")

        # In an isolated subprocess, verify that importing Init does not pull in GUI modules
        cmd = [
            sys.executable,
            "-c",
            (
                "import sys, freecad_smbs.Init; "
                "forbidden = ['FreeCADGui', 'PySide', 'PySide6.QtWidgets', 'PySide2.QtWidgets', 'PyQt5.QtWidgets', 'PyQt6.QtWidgets']; "
                "loaded = [m for m in forbidden if m in sys.modules]; "
                "assert not loaded, f'Loaded forbidden GUI modules: {loaded}'; "
                "print('OK')"
            ),
        ]
        import subprocess
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            f"Headless import failed or loaded GUI modules in fresh process: {proc.stderr}",
        )

    def test_viewer_cli_unchanged(self):
        """Verify smbs_core.viewer CLI continues to function independently."""
        try:
            from smbs_core.viewer.cli import main
        except ModuleNotFoundError as e:
            if "OCC" in str(e):
                self.skipTest("OCC not installed in current interpreter; test requires FreeCAD python")
            raise

        # Invoking with --help should return code 2 (as designed by CLI parser catch)
        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["--help"])
            self.assertEqual(code, 2)

        # Invoking view on nonexistent directory should return 2
        with patch("sys.stderr", new_callable=io.StringIO):
            code_view = main(["view", "/nonexistent/test_dir_123"])
            self.assertEqual(code_view, 2)
