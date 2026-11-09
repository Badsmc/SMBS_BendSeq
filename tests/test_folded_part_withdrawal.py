"""Regression tests for real-section lateral withdrawal probe geometry.

Actual FreeCAD section extraction requires the user acceptance run.
"""
import unittest

from smbs_core.kinematics2d.contact_oracle_2d import ContactStatus
from tools.run_folded_part_withdrawal import probe_lateral_withdrawal


def rect(x0, y0, x1, y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1))


class FoldedPartWithdrawalTests(unittest.TestCase):
    def test_crosses_blocker_between_clear_endpoints(self):
        # Sheet at negative s, obstacle around zero s, move to positive s.
        sheet = (("folded_panel", rect(-6,0,-5,1)),)
        punch = (rect(-1,-1,1,2), ())
        die = (rect(20,-10,22,-8), ())
        report = probe_lateral_withdrawal(
            sheet, punch, die, distance_mm=12, direction=1,
            sample_count=25,
        )
        self.assertEqual(report["samples"][0]["status"], "CLEAR")
        self.assertEqual(report["samples"][-1]["status"], "CLEAR")
        self.assertEqual(report["status"], "SAMPLED_2D_INTERFERENCE")
        collisions = [s for s in report["samples"] if s["status"] == "INTERFERENCE"]
        self.assertTrue(collisions)
        self.assertTrue(any(
            f["tool"] == "PUNCH" and f["component"] == "folded_panel"
            for sample in collisions for f in sample["findings"]
        ))

    def test_both_directions_can_differ(self):
        sheet = (("folded_panel", rect(0,0,1,1)),)
        punch = (rect(4,0,5,1), ())
        die = (rect(0,-5,1,-4), ())
        positive = probe_lateral_withdrawal(
            sheet, punch, die, distance_mm=8, direction=1,
            sample_count=33,
        )
        negative = probe_lateral_withdrawal(
            sheet, punch, die, distance_mm=8, direction=-1,
            sample_count=33,
        )
        self.assertEqual(positive["status"], "SAMPLED_2D_INTERFERENCE")
        self.assertEqual(negative["status"], "SAMPLED_2D_CLEAR_NOT_CERTIFIED")

    def test_missing_section_is_unverified(self):
        report = probe_lateral_withdrawal(
            (), (rect(0,0,1,1), ()), (rect(0,-2,1,-1), ())
        )
        self.assertEqual(report["status"], "UNVERIFIED_NO_PART_SECTION")

    def test_bad_sampling_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sample_count"):
            probe_lateral_withdrawal(
                (("panel", rect(0,0,1,1)),),
                (rect(2,0,3,1), ()), (rect(2,-3,3,-2), ()),
                sample_count=1,
            )


if __name__ == "__main__":
    unittest.main()
