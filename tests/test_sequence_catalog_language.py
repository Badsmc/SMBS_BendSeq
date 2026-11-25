"""The headless catalog accepts a UI translator without importing FreeCAD."""
import unittest

from freecad_smbs.ui.localization import tr
from freecad_smbs.ui.localization import tr_message
from smbs_core.kinematics2d.sequence_catalog import parse_diagnostic


class SequenceCatalogLanguageTest(unittest.TestCase):
    def test_warning_follows_ui_language(self):
        diagnostic = {
            "candidates": [{
                "sequence": ["B01"],
                "steps": [{"bend_id": "B01", "evidence": "WARNING"}],
            }]
        }
        english = parse_diagnostic(diagnostic, "report.json", translate=lambda value: tr(value, language="en"))
        russian = parse_diagnostic(diagnostic, "report.json", translate=lambda value: tr(value, language="ru"))
        self.assertEqual(english[0].warnings, ("B01: RETRACT/conditional 2D check",))
        self.assertEqual(russian[0].warnings, ("B01: RETRACT/условная 2D-проверка",))

    def test_blocker_reason_is_translated(self):
        diagnostic = {
            "selected_sequence": ["B01"], "steps": [],
            "first_blocker": {"bend_id": "B01", "reason": "Гиб B01 указан несколько раз."},
        }
        entries = parse_diagnostic(diagnostic, "manual.json",
                                   translate=lambda value: tr_message(value, language="en"))
        self.assertIn("Bend B01 is listed more than once.", entries[0].warnings[0])
