"""Regression for native OCC hot paths: proven AABB clearance and same-Common Conditional.

These tests intentionally avoid importing FreeCAD. Real OCC and production
throughput still require a FreeCAD runtime with representative FCStd assets.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, EndZoneCollisionPolicy, PairClearanceResult,
    PunchCollisionZoneClassification, check_pair_clearance,
    validate_workpiece_tooling_pose,
)
from smbs_core.kinematics2d.native_kernel_metrics import count_native_kernel_calls


def box(x0, x1, y0=-2., y1=2., z0=-2., z1=2.):
    return SimpleNamespace(
        XMin=x0, XMax=x1, YMin=y0, YMax=y1, ZMin=z0, ZMax=z1,
    )


def shape(bbox, *, vol=100., parts=None):
    return SimpleNamespace(
        BoundBox=bbox, Volume=vol, Solids=parts or [],
        isNull=lambda: False, isValid=lambda: True,
    )


class NativeCollisionFastPathTests(unittest.TestCase):
    def test_aabb_gap_above_contact_threshold_is_clear_without_native_distance(self):
        # 0.6 mm separation was inside the old arbitrary 5 mm window.
        left, right = shape(box(-5., 0.)), shape(box(0.6, 5.))
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance"
        ) as measured_dist, patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common"
        ) as common:
            result = check_pair_clearance(left, right, near_tolerance_mm=0.5)
        self.assertIs(result.status, CollisionClass.CLEAR)
        self.assertEqual(result.reason, "aabb_separated")
        self.assertTrue(result.is_lower_bound)
        self.assertEqual(result.distance_queries_count, 0)
        self.assertEqual(result.common_queries_count, 0)
        measured_dist.assert_not_called()
        common.assert_not_called()

    def test_aabb_at_threshold_is_not_wrongly_discarded(self):
        # Strictly above threshold only: an exactly marginal gap stays
        # eligible for exact distance and avoids an accidental false CLEAR.
        left, right = shape(box(-5., 0.)), shape(box(0.5, 5.))
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.5, None, None),
        ) as measured_dist, patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=shape(box(0., 0.), vol=0.),
        ) as measured_common:
            result = check_pair_clearance(left, right, near_tolerance_mm=0.5)
        self.assertNotEqual(result.reason, "aabb_separated")
        self.assertEqual(result.distance_queries_count, 1)
        self.assertEqual(result.common_queries_count, 1)
        measured_dist.assert_called_once()
        measured_common.assert_called_once()

    def test_small_positive_aabb_gap_needs_distance_but_not_common(self):
        # Previously every 0.2mm near-contact test paid for Part.common().
        left, right = shape(box(-5., 0.)), shape(box(0.2, 5.))
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.2, None, None),
        ) as distance, patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common"
        ) as common:
            result = check_pair_clearance(left, right, near_tolerance_mm=0.5)
        self.assertIs(result.status, CollisionClass.NEAR_CONTACT)
        self.assertEqual(result.distance_mm, 0.2)
        self.assertEqual(result.overlap_volume_mm3, 0.0)
        self.assertEqual(result.distance_queries_count, 1)
        self.assertEqual(result.common_queries_count, 0)
        distance.assert_called_once()
        common.assert_not_called()

    def test_subcontact_gap_remains_touching_not_false_clear(self):
        left, right = shape(box(-5., 0.)), shape(box(0.00005, 5.))
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.00005, None, None),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common"
        ) as common:
            result = check_pair_clearance(left, right)
        self.assertIs(result.status, CollisionClass.TOUCHING)
        self.assertEqual(result.distance_queries_count, 1)
        common.assert_not_called()

    def test_conditional_native_uses_one_exact_common_not_two(self):
        sheet = shape(box(-20., 20.))
        punch = shape(box(-100., 100.))
        left = shape(box(-100., -95.), vol=60.)
        right = shape(box(95., 100.), vol=40.)
        exact_common = shape(box(-100., 100.), vol=100., parts=[left, right])
        die = shape(box(-100., 100.))
        clear_die = PairClearanceResult(
            CollisionClass.CLEAR, "safe_die", 2., 0., 2., 0.,
        )
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.0, None, None),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=exact_common,
        ) as common, count_native_kernel_calls():
            result = validate_workpiece_tooling_pose(
                sheet, punch, die, cached_die_result=clear_die,
                end_zone_policy=EndZoneCollisionPolicy(
                    allow_end_zone_collision=True,
                    end_zone_length_mm=10.,
                ),
            )
        self.assertEqual(common.call_count, 1)
        self.assertIs(result.status, CollisionClass.PENETRATION)
        self.assertFalse(result.is_safe)
        self.assertTrue(result.is_conditional_safe)
        self.assertEqual(result.conditional_status, "CONDITIONAL")
        self.assertEqual(result.punch_result.common_queries_count, 1)
        self.assertEqual(
            result.punch_end_zone_analysis.classification,
            PunchCollisionZoneClassification.END_ZONE_ONLY,
        )
        self.assertIsNotNone(result.punch_result.overlap_bbox_xyz_mm)

    def test_significant_central_remains_physically_blocking(self):
        sheet = shape(box(-20., 20.))
        punch = shape(box(-100., 100.))
        end = shape(box(-100., -95.), vol=40.)
        central = shape(box(-20., 20.), vol=2.)
        common_shape = shape(box(-100., 20.), vol=42., parts=[end, central])
        die = shape(box(-100., 100.))
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.0, None, None),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common_shape,
        ) as common:
            result = validate_workpiece_tooling_pose(
                sheet, punch, die, cached_die_result=PairClearanceResult(
                    CollisionClass.CLEAR, "die_clear", 1., 0., 1., 0.,
                ),
                end_zone_policy=EndZoneCollisionPolicy(
                    allow_end_zone_collision=True, end_zone_length_mm=10.,
                ),
            )
        self.assertEqual(common.call_count, 1)
        self.assertIs(result.status, CollisionClass.PENETRATION)
        self.assertFalse(result.is_safe)
        self.assertFalse(result.is_conditional_safe)
        self.assertEqual(
            result.punch_end_zone_analysis.classification,
            PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE,
        )

    def test_enclosed_solids_still_run_common_even_if_exact_distance_positive(self):
        sheet = shape(box(-100., 100.))
        punch = shape(box(-2., 2.))
        common_shape = shape(box(-2., 2.), vol=25.)
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(4.0, None, None),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common_shape,
        ) as common:
            result = check_pair_clearance(sheet, punch)
        self.assertIs(result.status, CollisionClass.PENETRATION)
        self.assertEqual(result.reason, "enclosed_positive_common_volume")
        self.assertEqual(common.call_count, 1)

    def test_unclassifiable_endzone_does_not_turn_into_clear(self):
        sheet = shape(box(-20., 20.))
        punch = shape(box(-100., 100.))
        common_shape = shape(box(-20., 20.), vol=12., parts=[
            shape(box(-20., 20.), vol=12.),
        ])
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_distance",
            return_value=(0.0, None, None),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common_shape,
        ) as common:
            result = validate_workpiece_tooling_pose(
                sheet, punch, object(),
                cached_die_result=PairClearanceResult(
                    CollisionClass.CLEAR, "die_clear", 1., 0., 1., 0.,
                ),
                end_zone_policy=EndZoneCollisionPolicy(
                    allow_end_zone_collision=True, end_zone_length_mm=500.,
                ),
            )
        self.assertEqual(common.call_count, 1)
        self.assertIs(result.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(result.is_conditional_safe)
        self.assertFalse(result.is_safe)


if __name__ == "__main__":
    unittest.main()
