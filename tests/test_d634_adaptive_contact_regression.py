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
        _depth, states, unresolved, diagnostics = self.check_interval(
            lambda u: _state(u, CollisionClass.PENETRATION)
        )
        self.assertGreater(len(states), 2)
        self.assertTrue(unresolved)
        self.assertTrue(any("MAX_RECURSION_DEPTH_EXCEEDED" in d
                            for d in diagnostics))


if __name__ == "__main__":
    unittest.main()
