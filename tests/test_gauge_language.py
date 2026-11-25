"""Visual backgauge diagnostics remain language independent in core."""
import unittest
from types import SimpleNamespace

from freecad_smbs.ui.localization import tr
from smbs_core.kinematics2d.gauge_preview import visual_gauge_target


class GaugeLanguageTest(unittest.TestCase):
    def test_station_warning_uses_selected_translator(self):
        model = SimpleNamespace(automatic=True, edge_clearance_mm=2.0)
        english = visual_gauge_target(model, "B01", ((10.0, 0.0), None),
                                      translate=lambda source: tr(source, language="en"))
        russian = visual_gauge_target(model, "B01", ((10.0, 0.0), None))
        self.assertIn("gauge 2 is outside the workpiece outline along X", english[1])
        self.assertIn("упор 2 вне контура детали по X", russian[1])
