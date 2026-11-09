"""Headless checks for GUI file diagnostics; no FreeCAD or Qt required."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from freecad_smbs.ui.diagnostics_logging import _pressbrake_user_data_directory


class GuiDiagnosticsTests(unittest.TestCase):
    def test_windows_location_respects_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("freecad_smbs.ui.diagnostics_logging.os.name", "nt"), \
                 patch("freecad_smbs.ui.diagnostics_logging.sys.platform", "win32"), \
                 patch.dict(os.environ, {"APPDATA": tmp}, clear=False):
                self.assertEqual(
                    _pressbrake_user_data_directory(),
                    Path(tmp) / "FreeCAD" / "v1-1",
                )

    def test_linux_location_respects_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("freecad_smbs.ui.diagnostics_logging.os.name", "posix"), \
                 patch("freecad_smbs.ui.diagnostics_logging.sys.platform", "linux"), \
                 patch.dict(os.environ, {"XDG_DATA_HOME": tmp}, clear=False):
                self.assertEqual(
                    _pressbrake_user_data_directory(),
                    Path(tmp) / "FreeCAD" / "v1-1",
                )


if __name__ == "__main__":
    unittest.main()
