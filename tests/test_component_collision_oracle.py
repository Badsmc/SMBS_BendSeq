"""Pure-Python contracts for the fail-closed component collision screen."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from smbs_core.kinematics2d.component_collision_oracle import (
    ComponentVerdict, screen_component_penetration,
)


def shape(x0, x1):
    return SimpleNamespace(
        BoundBox=SimpleNamespace(XMin=x0, XMax=x1, YMin=0, YMax=1, ZMin=0, ZMax=1),
        isNull=lambda: False,
    )


class ComponentOracleTests(unittest.TestCase):
    def test_aabb_disjoint_never_claims_clear(self):
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume") as common:
            r = screen_component_penetration([("P01", shape(0, 1))], [("PUNCH", shape(2, 3))],
                                             volume_tolerance_mm3=1e-4)
        self.assertEqual(r.verdict, ComponentVerdict.FALLBACK_REQUIRED)
        self.assertEqual(r.aabb_rejected, 1)
        common.assert_not_called()

    def test_exact_positive_component_reject(self):
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume", return_value=0.3):
            r = screen_component_penetration([("bend_zone:B09", shape(0, 2))],
                                             [("PUNCH", shape(1, 3))], volume_tolerance_mm3=1e-4)
        self.assertEqual(r.verdict, ComponentVerdict.PROVEN_PENETRATION)
        self.assertEqual(r.hit.component_id, "bend_zone:B09")
        self.assertEqual(r.hit.tool_id, "PUNCH")

    def test_micro_overlaps_do_not_claim_clear_or_collision(self):
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume", return_value=0.00006):
            r = screen_component_penetration([("P01", shape(0, 2)), ("P02", shape(0, 2))],
                                             [("DIE", shape(1, 3))], volume_tolerance_mm3=0.0001)
        self.assertEqual(r.verdict, ComponentVerdict.FALLBACK_REQUIRED)
        self.assertEqual(r.common_calls, 2)

    def test_kernel_exception_fails_closed(self):
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume", side_effect=RuntimeError("OCC")):
            r = screen_component_penetration([("P01", shape(0, 2))],
                                             [("PUNCH", shape(1, 3))], volume_tolerance_mm3=1e-4)
        self.assertEqual(r.verdict, ComponentVerdict.FALLBACK_REQUIRED)
        self.assertIn("uncertain", r.reason)

    def test_invalid_tolerance_is_not_a_pass(self):
        r = screen_component_penetration([("P01", shape(0, 2))], [("PUNCH", shape(1, 3))],
                                         volume_tolerance_mm3=float("nan"))
        self.assertEqual(r.verdict, ComponentVerdict.FALLBACK_REQUIRED)

    def test_duplicate_component_id_fails_closed(self):
        r = screen_component_penetration([("P01", shape(0, 1)), ("P01", shape(0, 1))],
                                         [("PUNCH", shape(2, 3))], volume_tolerance_mm3=1e-4)
        self.assertEqual(r.verdict, ComponentVerdict.FALLBACK_REQUIRED)


if __name__ == "__main__":
    unittest.main()
