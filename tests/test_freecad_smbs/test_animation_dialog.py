"""Headless-safe tests for animation_dialog module and get_player bridge (S.70)."""

import unittest
from unittest.mock import MagicMock, patch

from freecad_smbs.integration.smbs_bridge import get_player
from freecad_smbs.ui.animation_dialog import (
    OBJECT_NAME,
    TICK_INTERVAL_MS_DEFAULT,
    _to_fc_shape,
    _update_feature,
)


class TestAnimationDialogHeadless(unittest.TestCase):
    def test_module_constants(self):
        self.assertEqual(OBJECT_NAME, "SMBS_Animation")
        self.assertEqual(TICK_INTERVAL_MS_DEFAULT, 500)

    def test_to_fc_shape_none(self):
        """None -> None (no Part import triggered)."""
        self.assertIsNone(_to_fc_shape(None))

    def test_animation_dialog_module_headless(self):
        """Importing animation_dialog must not pull FreeCAD/PySide."""
        import subprocess, sys

        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.animation_dialog; "
            "forbidden = ['FreeCAD','FreeCADGui','Part','PySide6','PySide2','PyQt5','PyQt6']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestGetPlayerBridge(unittest.TestCase):
    def test_get_player_lazy_imports(self):
        """smbs_bridge top-level import must not load OCC."""
        import subprocess, sys

        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.integration.smbs_bridge; "
            "forbidden = ['OCC','OCP']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_get_player_raises_on_bad_09(self):
        """get_player eagerly builds frames -> raises PlayerError on malformed verification."""
        try:
            from smbs_core.viewer.player import PlayerError
        except ModuleNotFoundError as e:
            if "OCC" in str(e):
                self.skipTest("OCC not installed in current interpreter; test requires FreeCAD python")
            raise

        mock_artifacts = MagicMock()
        mock_artifacts.verification_result = {"payload": {}}  # missing required fields
        with patch("smbs_core.viewer.graph_builder.build_graphs", return_value=MagicMock()), \
             patch("smbs_core.geometry_backend.backends.pythonocc_backend.PythonOCCBackend", return_value=MagicMock()):
            with self.assertRaises(PlayerError):
                get_player(mock_artifacts)


if __name__ == "__main__":
    unittest.main()
