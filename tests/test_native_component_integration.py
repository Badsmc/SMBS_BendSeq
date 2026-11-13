"""Headless D6 wiring: a proven component blocker skips multiFuse; others do not."""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig, AdaptiveTrajectoryValidator3D,
)
from smbs_core.kinematics2d.native_collision_3d import CollisionClass, PairClearanceResult
from smbs_core.kinematics2d.native_component_collision_3d import (
    ComponentScreenResult, ProvenComponentHit,
)


class Shape:
    def copy(self):
        return Shape()

    def transformShape(self, matrix):
        return self


class Fold:
    def __init__(self):
        self.angles = {"B01": 0.0}
        self.ordered_bends = [
            SimpleNamespace(bend_zone=SimpleNamespace(thickness_mm=1.5)),
        ]

    def set_angle(self, bend_id, angle):
        self.angles[bend_id] = float(angle)


class Matrix:
    def __init__(self, *args):
        self.args = args


def fake_placement():
    return SimpleNamespace(
        rotation=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
        translation=(0., 0., 0.),
    )


def completed_report():
    clear = PairClearanceResult(
        CollisionClass.CLEAR, "aabb_separated",
        10.0, 0.0, 10.0, 0.0,
    )
    return SimpleNamespace(
        status=CollisionClass.CLEAR,
        is_safe=True,
        punch_result=clear,
        die_result=clear,
        is_conditional_safe=False,
        conditional_status=None,
        punch_end_zone_analysis=None,
    )


class X1IntegrationTests(unittest.TestCase):
    def make_validator(self):
        panel_build = SimpleNamespace(
            panels=[SimpleNamespace(panel_id="P01"), SimpleNamespace(panel_id="P02")],
            panel_faces=[object(), object()],
        )
        punch = SimpleNamespace(machine_shape=Shape())
        die = SimpleNamespace(
            machine_shape=Shape(), v_opening_mm=12.0,
            effective_v_opening_mm=12.0,
        )
        config = AdaptiveSamplingConfig(component_collision_rejection=True)
        return AdaptiveTrajectoryValidator3D(
            Fold(), panel_build, punch, die, config=config,
        )

    def evaluate(self, rejection):
        val = self.make_validator()
        hit = (ProvenComponentHit("P09", "DIE", 12.5, None, False)
               if rejection else None)
        screen = ComponentScreenResult(
            hit=hit, candidate_pairs=2, aabb_rejected_pairs=40,
            common_calls=2, elapsed_ms=2.5,
            fallback_reason=("COMPONENT_PROVEN_PENETRATION" if rejection
                             else "needs_full_sheet_oracle"),
        )
        prefix = "smbs_core.kinematics2d.motion_trajectory_3d."
        with ExitStack() as stack:
            stack.enter_context(patch.dict(
                "sys.modules", {"FreeCAD": SimpleNamespace(Matrix=Matrix)},
            ))
            stack.enter_context(patch(
                prefix + "WorkpiecePlacement3D.compute_placement",
                return_value=fake_placement(),
            ))
            stack.enter_context(patch(
                prefix + "_panel_build_geometry_signature",
                return_value=("source",),
            ))
            stack.enter_context(patch(
                prefix + "_die_geometry_signature", return_value=("die",),
            ))
            stack.enter_context(patch(
                prefix + "_punch_geometry_signature", return_value=("punch",),
            ))
            stack.enter_context(patch(
                prefix + "build_multi_bend_sheet_components",
                return_value=object(),
            ))
            stack.enter_context(patch(
                prefix + "set_punch_ram_z",
                return_value=SimpleNamespace(machine_shape=Shape()),
            ))
            stack.enter_context(patch(
                prefix + "screen_native_sheet_components",
                return_value=screen,
            ))
            full = stack.enter_context(patch(
                prefix + "build_multi_bend_sheet_solid",
            ))
            joined = stack.enter_context(patch(
                prefix + "join_multi_bend_sheet_components",
                return_value=SimpleNamespace(shape=Shape()),
            ))
            exact = stack.enter_context(patch(
                prefix + "validate_workpiece_tooling_pose",
                return_value=completed_report(),
            ))
            result = val.create_state_snapshot(
                "B01", "RETRACT", 0.5, 45.0, -1.0, 0.0, {},
            )
        return val, result, full, joined, exact

    def test_positive_native_component_intersection_skips_full_sheet_and_full_oracle(self):
        val, result, full, joined, exact = self.evaluate(True)
        self.assertFalse(result.is_safe)
        self.assertEqual(result.collision_report.status, CollisionClass.PENETRATION)
        self.assertEqual(result.collision_report.die_result.overlap_volume_mm3, 12.5)
        self.assertEqual(val.total_solid_reconstructions, 0)
        self.assertEqual(val.actual_common_queries, 2)
        full.assert_not_called()
        joined.assert_not_called()
        exact.assert_not_called()

    def test_nonblocking_or_ambiguous_screen_reuses_components_and_runs_exact_oracle(self):
        val, result, full, joined, exact = self.evaluate(False)
        self.assertTrue(result.is_safe)
        self.assertEqual(result.collision_report.status, CollisionClass.CLEAR)
        self.assertEqual(val.total_solid_reconstructions, 1)
        full.assert_not_called()
        joined.assert_called_once()
        exact.assert_called_once()


if __name__ == "__main__":
    unittest.main()
