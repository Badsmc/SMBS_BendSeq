"""Headless contract tests for the real-tool negative control runner.

Fixture tool profiles are deliberately simple to isolate runner logic.
An additional FreeCAD run loads the actual user-selected punch and die.
"""
import unittest
from unittest.mock import patch

from tools.run_tooling_negative_controls import run_negative_controls


def rect(x0,y0,x1,y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1))


class FakeTools:
    def select_punch(self, p):
        self.punch = p

    def select_die(self, d):
        self.die = d

    def punch_polygons_machine(self, arc_segments=24):
        return rect(-1,-2,1,0), ()

    def die_polygons_machine(self, arc_segments=24):
        return rect(-5,-10,5,-8), ()


class NegativeControlTests(unittest.TestCase):
    def test_clear_penetration_and_blocked_transfer(self):
        with patch("tools.run_tooling_negative_controls.ToolLibrary25D") as lib, patch(
            "tools.run_tooling_negative_controls.ToolingSetup2D", FakeTools
        ):
            lib.return_value.load_tool.side_effect = lambda *args: object()
            result = run_negative_controls()
        self.assertTrue(result["success"], result["cases"])
        self.assertEqual(result["passed"], 5)
        self.assertEqual(result["total"], 5)
        transfer = result["transfer_control"]
        samples = transfer["samples"]
        self.assertEqual(samples[0]["status"], "CLEAR")
        self.assertEqual(samples[-1]["status"], "CLEAR")
        self.assertEqual(samples[10]["status"], "INTERFERENCE")
        self.assertIn("NOT real Z part", transfer["scope"])

    def test_reject_missing_real_tool_geometry(self):
        class MissingTools(FakeTools):
            def punch_polygons_machine(self, arc_segments=24):
                return None
        with patch("tools.run_tooling_negative_controls.ToolLibrary25D") as lib, patch(
            "tools.run_tooling_negative_controls.ToolingSetup2D", MissingTools
        ):
            lib.return_value.load_tool.side_effect = lambda *args: object()
            with self.assertRaisesRegex(RuntimeError, "Both tool geometries"):
                run_negative_controls()


if __name__ == "__main__":
    unittest.main()
