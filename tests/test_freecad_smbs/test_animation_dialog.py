"""Headless-safe tests for animation_dialog module and get_player bridge (S.70)."""

import unittest
from unittest.mock import MagicMock, patch

from freecad_smbs.integration.smbs_bridge import get_player


class TestGetPlayerBridge(unittest.TestCase):
    def test_get_player_lazy_imports(self):
        """smbs_bridge top-level import must not load OCC."""
        import subprocess, sys
        cmd = [sys.executable, "-c",
               "import sys, freecad_smbs.integration.smbs_bridge; "
               "forbidden = ['OCC','OCP']; "
               "loaded = [m for m in forbidden if m in sys.modules]; "
               "assert not loaded, f'Loaded: {loaded}'; print('OK')"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_get_player_raises_on_bad_09(self):
        """get_player eagerly builds frames -> raises on malformed verification."""
        mock_artifacts = MagicMock()
        mock_artifacts.verification_result = {"payload": {}}  # missing required fields
        with patch("smbs_core.viewer.graph_builder.build_graphs", return_value=MagicMock()), \
             patch("smbs_core.geometry_backend.backends.pythonocc_backend.PythonOCCBackend", return_value=MagicMock()):
            with self.assertRaises(Exception):
                get_player(mock_artifacts)


if __name__ == "__main__":
    unittest.main()
