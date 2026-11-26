"""The tooling summary follows FreeCAD's selected language."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from freecad.bendseq.commands.pressbrake_2d import _tooling_status_text


class ToolingStatusLanguageTest(unittest.TestCase):
    def test_status_in_both_languages(self):
        setup = SimpleNamespace(
            punch=SimpleNamespace(name="P1"), die=SimpleNamespace(name="D1"),
            geometry_status_summary=lambda: "VERIFIED",
            match_active_bend_length=True, active_bend_length_mm=120.0,
        )
        with patch("freecad.bendseq.ui.localization.interface_language", return_value="en"):
            english = _tooling_status_text(setup)
        with patch("freecad.bendseq.ui.localization.interface_language", return_value="ru"):
            russian = _tooling_status_text(setup)
        self.assertIn("Punch: P1 | Die: D1 | Geometry: verified", english)
        self.assertIn("Tool length: VIRTUAL=120.0 mm", english)
        self.assertIn("Пуансон: P1 | Матрица: D1 | Геометрия: проверена", russian)
