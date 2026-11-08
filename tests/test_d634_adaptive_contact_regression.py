"""Regression tests for D6.3.4 bounded adaptive interval refinement.

Stable tool contact must not consume the entire recursion budget, but genuine
penetration/rapid changes must not be silently treated as converged.
"""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_collision_3d import CollisionClass
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig, AdaptiveTrajectoryValidator3D,
)


def _state(u, status=CollisionClass.NEAR_CONTACT, distance=0.0):
    pair = SimpleNamespace(status=status, distance_mm=distance)
    report = SimpleNamespace(
        status=status, punch_result=pair, die_result=pair,
    )
    return SimpleNamespace(
        path_u=u, is_safe=(status is not CollisionClass.PENETRATION),
        signed_angle_deg=0.0, ram_z_mm=1.5, phase="CONTACT",
        completed_angles={}, collision_report=report,
        workpiece_placement=SimpleNamespace(
            translation=(0.0, 0.0, 0.0),
            rotation=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)),
        ),
    )


class AdaptiveContactRegressionTests(unittest.TestCase):
    def setUp(self):
        self.validator = AdaptiveTrajectoryValidator3D.__new__(
            AdaptiveTrajectoryValidator3D
        )
        self.validator.config = AdaptiveSamplingConfig(
            max_recursion_depth=4, max_evaluated_states_per_phase=30,
            max_phase_time_s=10.0,
        )
        self.validator.total_subdivisions = 0

    def check_interval(self, sample_fn):
        left = sample_fn(0.)
        right = sample_fn(1.)
        seen = [left]
        unresolved, diagnostics = [], []
        depth = self.validator.validate_adaptive_interval(
            left, right, sample_fn, 1, seen, unresolved, diagnostics,
            time.perf_counter(),
        )
        return depth, seen, unresolved, diagnostics

    def test_stable_near_contact_converges_after_mandatory_midpoint(self):
        depth, states, unresolved, diagnostics = self.check_interval(
            lambda u: _state(u)
        )
        self.assertEqual(depth, 1)
        self.assertEqual([s.path_u for s in states], [0.0, 0.5])
        self.assertEqual(unresolved, [])
        self.assertEqual(diagnostics, [])
        self.assertEqual(self.validator.total_subdivisions, 1)

    def test_clearance_transition_requires_more_samples(self):
        def sample(u):
            return _state(
                u,
                status=(CollisionClass.NEAR_CONTACT if u >= 0.5
                        else CollisionClass.CLEAR),
                distance=(0.0 if u >= 0.5 else 3.0),
            )
        _depth, states, _unresolved, _diagnostics = self.check_interval(sample)
        self.assertGreater(len(states), 2)

    def test_penetration_remains_unresolved_not_converged(self):
        # With early_stop disabled, penetration keeps recursing until max depth
        self.validator.config.early_stop_on_blocking_collision = False
        _depth, states, unresolved, diagnostics = self.check_interval(
            lambda u: _state(u, CollisionClass.PENETRATION)
        )
        self.assertGreater(len(states), 2)
        self.assertTrue(unresolved)
        self.assertTrue(any("MAX_RECURSION_DEPTH_EXCEEDED" in d
                            for d in diagnostics))

    def test_early_stop_inside_adaptive_interval_on_blocking_penetration(self):
        # Priority B / ChatGPT item 2: early stop inside adaptive phase
        self.validator.config.early_stop_on_blocking_collision = True

        def sample_fatal_penetration(u):
            pair = SimpleNamespace(
                status=CollisionClass.PENETRATION,
                distance_mm=0.0,
                overlap_volume_mm3=50.0 if u >= 0.5 else 0.0,
            )
            report = SimpleNamespace(
                status=CollisionClass.PENETRATION if u >= 0.5 else CollisionClass.CLEAR,
                punch_result=pair,
                die_result=pair,
            )
            return SimpleNamespace(
                path_u=u,
                is_safe=(u < 0.5),
                is_conditional_safe=False,
                signed_angle_deg=0.0,
                ram_z_mm=1.5,
                phase="BENDING",
                completed_angles={},
                collision_report=report,
                workpiece_placement=SimpleNamespace(
                    translation=(0.0, 0.0, 0.0),
                    rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                ),
            )

        left = sample_fatal_penetration(0.0)
        right = sample_fatal_penetration(1.0)
        seen = [left]
        unresolved, diagnostics = [], []
        uninspected = []

        depth = self.validator.validate_adaptive_interval(
            left,
            right,
            sample_fatal_penetration,
            1,
            seen,
            unresolved,
            diagnostics,
            time.perf_counter(),
            uninspected=uninspected,
        )

        # Early stop must trigger at midpoint u=0.5:
        # seen has only [left, mid]; no deeper recursion occurs!
        self.assertEqual(depth, 1)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1].path_u, 0.5)
        self.assertTrue(any("UNINSPECTED_INTERVAL_AFTER_FATAL_COLLISION" in d for d in diagnostics))
        self.assertEqual(uninspected, [(0.0, 1.0)])

    def test_conditional_does_not_early_stop_checking_other_states(self):
        # Invariant: CONDITIONAL must NOT stop checking other states
        self.validator.config.early_stop_on_blocking_collision = True

        def sample_conditional(u):
            pair = SimpleNamespace(
                status=CollisionClass.PENETRATION,
                distance_mm=0.0,
                overlap_volume_mm3=5.0,
            )
            ez = SimpleNamespace(
                classification=SimpleNamespace(value="END_ZONE_ONLY"),
                total_overlap_volume_mm3=5.0,
            )
            report = SimpleNamespace(
                status=CollisionClass.PENETRATION,
                punch_result=pair,
                die_result=SimpleNamespace(status=CollisionClass.CLEAR, distance_mm=10.0, overlap_volume_mm3=0.0),
            )
            return SimpleNamespace(
                path_u=u,
                is_safe=False,
                is_conditional_safe=True,  # Conditional end-zone
                punch_end_zone_analysis=ez,
                signed_angle_deg=0.0,
                ram_z_mm=1.5,
                phase="BENDING",
                completed_angles={},
                collision_report=report,
                workpiece_placement=SimpleNamespace(
                    translation=(0.0, 0.0, 0.0),
                    rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                ),
            )

        left = sample_conditional(0.0)
        right = sample_conditional(1.0)
        seen = [left]
        unresolved, diagnostics = [], []
        uninspected = []

        self.validator.validate_adaptive_interval(
            left,
            right,
            sample_conditional,
            1,
            seen,
            unresolved,
            diagnostics,
            time.perf_counter(),
            uninspected=uninspected,
        )
        # Because it's CONDITIONAL, it does NOT trigger early fatal stop
        self.assertFalse(any("UNINSPECTED_INTERVAL_AFTER_FATAL_COLLISION" in d for d in diagnostics))
        self.assertEqual(uninspected, [])


if __name__ == "__main__":
    unittest.main()
