"""Common-first penetration probe: strict equivalence and failure contracts.

No mocked boolean is allowed to *declare clearance* without the exact
distance fallback. Native Part/FreeCAD integration still requires live FCStd.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, check_pair_clearance,
)


def box():
    return SimpleNamespace(
        XMin=-100.0, XMax=100.0,
        YMin=-20.0, YMax=20.0,
        ZMin=-10.0, ZMax=10.0,
    )


class CommonFirstPenetrationTests(TestCase):
    def run_case(self, *, common_volume: float, distance: float,
                 prefer: bool, tolerance=1e-4, tool="PUNCH"):
        a = SimpleNamespace(BoundBox=box())
        b = SimpleNamespace(BoundBox=box())
        common = SimpleNamespace(
            Volume=common_volume,
            BoundBox=box(),
            isNull=lambda: False,
            isValid=lambda: True,
        )
        mod = "smbs_core.kinematics2d.native_collision_3d."
        with patch(mod + "check_solid_validity", return_value=(True, "valid")):
            with patch(mod + "_compute_exact_distance_with_pruning",
                       return_value=distance) as d:
                with patch(mod + "measured_common",
                           return_value=common) as cm:
                    result = check_pair_clearance(
                        a, b, tool_label=tool,
                        prefer_overlap_first=prefer,
                        volume_tolerance_mm3=tolerance,
                        near_tolerance_mm=0.5,
                        contact_distance_tolerance_mm=1e-4,
                    )
        return result, d.call_count, cm.call_count

    def test_positive_common_skips_distance_without_changing_verdict(self):
        previous, d_before, c_before = self.run_case(
            common_volume=123.4, distance=0.0, prefer=False,
        )
        improved, d_after, c_after = self.run_case(
            common_volume=123.4, distance=0.0, prefer=True,
        )
        self.assertEqual(previous.status, CollisionClass.PENETRATION)
        self.assertEqual(improved.status, previous.status)
        self.assertEqual(improved.reason, previous.reason)
        self.assertAlmostEqual(improved.overlap_volume_mm3, 123.4)
        self.assertEqual(improved.distance_mm, 0.0)
        self.assertEqual((d_before, c_before), (1, 1))
        self.assertEqual((d_after, c_after), (0, 1))
        self.assertEqual(improved.distance_queries_count, 0)
        self.assertEqual(improved.common_queries_count, 1)
        self.assertEqual(improved.tool_label, "PUNCH")
        self.assertIsNotNone(improved.overlap_bbox_xyz_mm)

    def test_nonpenetrating_near_contact_runs_original_distance(self):
        previous, d_before, c_before = self.run_case(
            common_volume=0.0, distance=0.2, prefer=False,
        )
        improved, d_after, c_after = self.run_case(
            common_volume=0.0, distance=0.2, prefer=True,
        )
        self.assertEqual(previous.status, CollisionClass.NEAR_CONTACT)
        self.assertEqual(improved.status, previous.status)
        self.assertEqual(improved.reason, previous.reason)
        self.assertAlmostEqual(improved.distance_mm, previous.distance_mm)
        self.assertEqual((d_before, c_before), (1, 1))
        self.assertEqual((d_after, c_after), (1, 1))
        self.assertEqual(improved.distance_queries_count, 1)
        self.assertEqual(improved.common_queries_count, 1)

    def test_small_numerical_overlap_preserves_touching_not_false_penetration(self):
        previous, _, _ = self.run_case(
            common_volume=5e-5, distance=0.0, prefer=False,
        )
        improved, d_after, c_after = self.run_case(
            common_volume=5e-5, distance=0.0, prefer=True,
        )
        self.assertEqual(previous.status, CollisionClass.TOUCHING)
        self.assertEqual(improved.status, previous.status)
        self.assertEqual(improved.reason, previous.reason)
        self.assertEqual((d_after, c_after), (1, 1))

    def test_separated_after_overlap_falls_back_to_exact_distance(self):
        previous, _, _ = self.run_case(
            common_volume=0.0, distance=12.0, prefer=False,
        )
        improved, d_after, c_after = self.run_case(
            common_volume=0.0, distance=12.0, prefer=True,
        )
        self.assertEqual(previous.status, CollisionClass.CLEAR)
        self.assertEqual(improved.status, previous.status)
        self.assertEqual(improved.reason, previous.reason)
        self.assertEqual((d_after, c_after), (1, 1))

    def test_invalid_common_fails_closed_without_distance(self):
        mod = "smbs_core.kinematics2d.native_collision_3d."
        a, b = SimpleNamespace(BoundBox=box()), SimpleNamespace(BoundBox=box())
        with patch(mod + "check_solid_validity", return_value=(True, "valid")):
            with patch(mod + "_compute_exact_distance_with_pruning") as distance:
                with patch(mod + "measured_common", side_effect=ValueError("OCCT error")):
                    got = check_pair_clearance(
                        a, b, tool_label="PUNCH", prefer_overlap_first=True,
                    )
        self.assertEqual(got.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(got.status == CollisionClass.CLEAR)
        self.assertIn("OCCT error", got.reason)
        distance.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
