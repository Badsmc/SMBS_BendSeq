"""Machine-accurate operator-side preview for an unverified manual order."""
import unittest

from smbs_core.kinematics2d.loading_orientation_preview import (
    plan_preview_loading_orientations,
)
from tests.test_workpiece_placement_3d import WorkpiecePlacement3DTests


class OperatorFacingManualPreviewTests(unittest.TestCase):
    def setUp(self):
        source = WorkpiecePlacement3DTests(
            "test_placement_is_proper_rigid_isometry"
        )
        source.setUp()
        self.state = source.fold_state
        self.verts = {
            **source.verts,
            "P00": ((0.0, -750.0), (100.0, -750.0),
                    (100.0, 50.0), (0.0, 50.0)),
            "P01": ((0.0, 52.0), (100.0, 52.0),
                    (100.0, 64.0), (0.0, 64.0)),
        }

    def test_long_operator_side_selected_for_manual_preview(self):
        before = dict(self.state.angles)
        before_transforms = dict(self.state.transforms)
        entries = plan_preview_loading_orientations(
            self.state, self.verts, ("B01",),
        )
        self.assertEqual(len(entries), 1)
        result = entries[0]
        self.assertTrue(result.reverse_axis)
        self.assertGreater(result.operator_reach_mm, 700.0)
        self.assertLess(result.rear_reach_mm, 110.0)
        self.assertEqual(result.status, "ERGONOMIC_ONLY_NOT_CAD_VALIDATED")
        self.assertEqual(before, self.state.angles)
        self.assertEqual(before_transforms, self.state.transforms)

    def test_bad_order_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_preview_loading_orientations(
                self.state, self.verts, ("B01", "B01"),
            )
        with self.assertRaises(ValueError):
            plan_preview_loading_orientations(
                self.state, self.verts, ("UNKNOWN",),
            )

    def test_preview_declaration_never_claims_cad_approval(self):
        entry = plan_preview_loading_orientations(
            self.state, self.verts, ("B01",),
        )[0].to_dict()
        self.assertIn("UNVERIFIED", entry["status"])
        self.assertNotIn("CERTIFIED", entry["status"])


if __name__ == "__main__":
    unittest.main()
