"""
test_preferences.py - Headless-safe unit tests for SMBS preferences and PreferencesCommand.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT


class TestPreferencesHeadless(unittest.TestCase):
    """Test preferences getters, setters, and headless safety."""

    def test_preferences_module_headless(self):
        """Importing preferences modules must not import FreeCADGui or Qt."""
        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.preferences_dialog, freecad_smbs.commands.preferences; "
            "forbidden = ['FreeCADGui', 'Part', 'PySide6', 'PySide2', 'PyQt5', 'PyQt6']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_get_and_set_preferences_mocked(self):
        """Preferences getter and setter work with ParamGet or dictionary fallback."""
        from freecad_smbs.ui.preferences_dialog import (
            get_smbs_preferences,
            set_smbs_preferences,
        )

        # In pure headless test without FreeCAD ParamGet
        prefs = get_smbs_preferences()
        self.assertIn("tooling_library_path", prefs)
        self.assertIn("search_algorithm", prefs)
        self.assertIn("unknown_policy", prefs)
        self.assertIn("tooling_set_id", prefs)

        # Update preferences
        new_prefs = {
            "tooling_library_path": "/tmp/custom_toollib",
            "search_algorithm": "dfs",
            "unknown_policy": "EXPLORATORY",
            "tooling_set_id": "test_set_123",
        }
        set_smbs_preferences(new_prefs)
        updated = get_smbs_preferences()
        self.assertEqual(updated["tooling_library_path"], "/tmp/custom_toollib")
        self.assertEqual(updated["search_algorithm"], "dfs")
        self.assertEqual(updated["unknown_policy"], "EXPLORATORY")
        self.assertEqual(updated["tooling_set_id"], "test_set_123")

    def test_preferences_command_metadata(self):
        """PreferencesCommand exposes proper menu and icon resources."""
        from freecad_smbs.commands.preferences import PreferencesCommand

        cmd = PreferencesCommand()
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Настройки…")
        self.assertIn("SMBS", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())
        self.assertTrue(cmd.IsActive())


if __name__ == "__main__":
    unittest.main()
