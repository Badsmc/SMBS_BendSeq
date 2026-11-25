"""Status icons must follow the FreeCAD language without implying safety."""
import unittest

from freecad_smbs.ui.pressbrake_theme import classify_status


class StatusClassificationTest(unittest.TestCase):
    def test_english_states(self):
        cases = (
            ("Checking bend B01", "running"),
            ("AUTO PLAY: Full Cycle | bend B01", "running"),
            ("AUTO PLAY completed: 10 bends", "success"),
            ("FAST: no candidates found in 30 s. This does not prove bending is impossible.", "warning"),
            ("2D check unavailable: tooling is missing", "error"),
            ("UNVERIFIED: collision check has not been run", "unverified"),
            ("Preliminary result: 3D collision not checked", "warning"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(classify_status(message)[0], expected)
