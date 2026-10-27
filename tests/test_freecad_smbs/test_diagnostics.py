"""
test_diagnostics.py - Headless-safe unit tests for SMBS environment diagnostics and DiagnosticsCommand.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestDiagnosticsHeadless(unittest.TestCase):
    """Test diagnostics collector and DiagnosticsCommand."""

    def test_diagnostics_module_headless(self):
        """Importing diagnostics modules must not import FreeCADGui or Qt."""
        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.diagnostics_dialog, freecad_smbs.commands.diagnostics; "
            "forbidden = ['FreeCADGui', 'Part', 'PySide6', 'PySide2', 'PyQt5', 'PyQt6']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_collect_environment_diagnostics(self):
        """Collector returns structured environment report."""
        from freecad_smbs.ui.diagnostics_dialog import collect_environment_diagnostics

        diag = collect_environment_diagnostics()
        self.assertIn("freecad", diag)
        self.assertIn("sheetmetal", diag)
        self.assertIn("pythonocc", diag)
        self.assertIn("toollibrary", diag)
        self.assertIn("active_document", diag)
        self.assertIn("ready", diag)

        # Tool library checks canonical repo
        toollib = diag["toollibrary"]
        self.assertTrue(toollib["path_exists"])
        self.assertGreater(toollib["punches_count"], 0)
        self.assertGreater(toollib["dies_count"], 0)
        # KL-16: machine_frame.FCStd is present in toollibrary
        self.assertTrue(toollib["machine_frame_present"])

    def test_diagnostics_command_metadata(self):
        """DiagnosticsCommand exposes proper menu and icon resources."""
        from freecad_smbs.commands.diagnostics import DiagnosticsCommand

        cmd = DiagnosticsCommand()
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Диагностика")
        self.assertIn("диагностик", res["ToolTip"].lower())
        self.assertTrue(Path(res["Pixmap"]).exists())
        self.assertTrue(cmd.IsActive())


if __name__ == "__main__":
    unittest.main()
