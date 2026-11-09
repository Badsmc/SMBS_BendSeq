"""Headless checks for GUI file diagnostics; no FreeCAD or Qt required."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from freecad_smbs.ui.diagnostics_logging import _pressbrake_user_data_directory


class GuiDiagnosticsTests(unittest.TestCase):
    def test_xdg_data_home_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("freecad_smbs.ui.diagnostics_logging.sys.platform", "linux"), \
                 patch.dict(os.environ, {"XDG_DATA_HOME": tmp}, clear=False):
                self.assertEqual(
                    _pressbrake_user_data_directory(),
                    Path(tmp) / "FreeCAD" / "v1-1",
                )

    def test_macos_location_uses_application_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("freecad_smbs.ui.diagnostics_logging.sys.platform", "darwin"), \
                 patch("freecad_smbs.ui.diagnostics_logging.Path.home", return_value=Path(tmp)):
                self.assertEqual(
                    _pressbrake_user_data_directory(),
                    Path(tmp) / "Library" / "Application Support" / "FreeCAD" / "v1-1",
                )


if __name__ == "__main__":
    unittest.main()
