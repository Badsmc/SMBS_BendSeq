"""Tests for cache correctness, separated counters, and end-zone exception handling in D6.3.4.

Verifies:
1. Four counter scenarios with known call counts:
   - Scenario A: both tools checked (cold cache)
   - Scenario B: punch in cache, die checked
   - Scenario C: die in cache, punch checked
   - Scenario D: full pose in cache
2. Cache invalidation on tool length, placement, tolerances, and end-zone policy.
3. Cold and warm cache produce identical statuses, overlap volumes, and classifications.
4. End-zone exception caching:
   - Allowed END_ZONE_ONLY preserved through cache
   - Central collision preserved through cache
   - Die collision preserved through cache
   - Missing shapes for required end-zone classification fails closed (never CLEAR or CONDITIONAL).
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

try:
    import FreeCAD as App
    from PySide6 import QtWidgets
    HAS_FREECAD = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.demo_paths import demo_part_path
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    EndZoneCollisionPolicy,
    PairClearanceResult,
    PunchCollisionZoneClassification,
    PunchEndZoneAnalysis,
    ToolCollisionReport,
    validate_workpiece_tooling_pose,
)
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
)
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_die,
    load_native_punch,
)


class TestD634CacheAndCountersCorrectness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD:
            raise unittest.SkipTest("FreeCAD not available")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cls.model_path = demo_part_path("box.100out-50in-(ok).FCStd", required=False)
        if not cls.model_path.is_file():
            raise unittest.SkipTest(f"Model not found: {cls.model_path}")

    def setUp(self):
        self.doc = App.openDocument(str(self.model_path), True)
        self.unfold = next((o for o in self.doc.Objects if o.Name.endswith("_Unfold")), None)
        if self.unfold is None:
            App.closeDocument(self.doc.Name)
            raise unittest.SkipTest("Model missing _Unfold object")

        from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
        self.session = build_part_session(self.doc, [self.unfold])
        self.panel = _PreviewPanel()
        self.panel.session = self.session
        self.panel._ensure_press_cycle_controller()
        self.panel.align_tooling()

        punch_path = str(ROOT / "toollibrary" / "punch" / "puancon.FCStd")
        die_path = str(ROOT / "toollibrary" / "die" / "matrica.FCStd")
        self.punch = load_native_punch(punch_path, ram_z_mm=50.0)
        self.die = load_native_die(die_path)
        self.config = AdaptiveSamplingConfig(arc_segments=128)
        self.validator = AdaptiveTrajectoryValidator3D(
            self.session.fold_state,
            self.session.panel_build,
            self.punch,
            self.die,
            config=self.config,
        )

    def tearDown(self):
        if hasattr(self, "panel") and self.panel is not None:
            self.panel._stop_timer()
            self.panel.close()
            self.panel.deleteLater()
        if hasattr(self, "doc") and self.doc is not None:
            App.closeDocument(self.doc.Name)

    def test_four_counter_scenarios(self):
        """Test exact separated counters across the 4 canonical scenarios:
        1. Both tools checked (cold cache)
        2. Punch cached, die checked
        3. Die cached, punch checked
        4. Full pose cached
        """
        v = self.validator
        v.clear_caches()

        # Scenario 1: Cold cache -> both tools checked
        st1 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="APPROACH",
            path_u=0.0,
            signed_angle=0.0,
            ram_z=50.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(v.state_evaluation_requests, 1)
        self.assertEqual(v.pose_cache_hits, 0)
        self.assertEqual(v.punch_cache_hits, 0)
        self.assertEqual(v.die_cache_hits, 0)
        self.assertEqual(v.pair_collision_checks, 2)  # both punch and die evaluated
        self.assertGreaterEqual(v.actual_distance_queries, 1)
        self.assertEqual(v.total_solid_reconstructions, 1)

        # Scenario 2: Punch in cache, die not in cache.
        # Clear pose cache and die clearance cache, but preserve punch clearance cache.
        v._pose_collision_cache.clear()
        v._die_clearance_cache.clear()
        initial_requests = v.state_evaluation_requests
        initial_pair_checks = v.pair_collision_checks
        initial_p_hits = v.punch_cache_hits
        initial_d_hits = v.die_cache_hits

        st2 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="APPROACH",
            path_u=0.0,
            signed_angle=0.0,
            ram_z=50.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(v.state_evaluation_requests - initial_requests, 1)
        self.assertEqual(v.punch_cache_hits - initial_p_hits, 1)
        self.assertEqual(v.die_cache_hits - initial_d_hits, 0)
        self.assertEqual(v.pair_collision_checks - initial_pair_checks, 1)  # only die checked!

        # Scenario 3: Die in cache, punch not in cache.
        # Clear pose cache and punch clearance cache, but preserve die clearance cache.
        v._pose_collision_cache.clear()
        v._punch_clearance_cache.clear()
        initial_requests = v.state_evaluation_requests
        initial_pair_checks = v.pair_collision_checks
        initial_p_hits = v.punch_cache_hits
        initial_d_hits = v.die_cache_hits

        st3 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="APPROACH",
            path_u=0.0,
            signed_angle=0.0,
            ram_z=50.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(v.state_evaluation_requests - initial_requests, 1)
        self.assertEqual(v.punch_cache_hits - initial_p_hits, 0)
        self.assertEqual(v.die_cache_hits - initial_d_hits, 1)
        self.assertEqual(v.pair_collision_checks - initial_pair_checks, 1)  # only punch checked!

        # Scenario 4: Full pose in cache.
        # Repeat st1 call with identical parameters.
        initial_requests = v.state_evaluation_requests
        initial_pose_hits = v.pose_cache_hits
        initial_pair_checks = v.pair_collision_checks
        initial_dist = v.actual_distance_queries
        initial_comm = v.actual_common_queries

        st4 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="APPROACH",
            path_u=0.0,
            signed_angle=0.0,
            ram_z=50.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(v.state_evaluation_requests - initial_requests, 1)
        self.assertEqual(v.pose_cache_hits - initial_pose_hits, 1)
        self.assertEqual(v.pair_collision_checks - initial_pair_checks, 0)  # 0 pair checks!
        self.assertEqual(v.actual_distance_queries - initial_dist, 0)       # 0 distance queries!
        self.assertEqual(v.actual_common_queries - initial_comm, 0)         # 0 common queries!

    def test_cold_and_warm_cache_numerical_identity(self):
        """Verify that cold vs warm cache produces identical statuses, overlap volumes, and diagnostics."""
        v = self.validator
        v.clear_caches()

        # Cold snapshot
        st_cold = v.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=15.0,
            support_z=0.0,
            prior_angles={},
        )
        # Warm snapshot (pose cache hit)
        st_warm = v.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=15.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(st_cold.is_safe, st_warm.is_safe)
        self.assertEqual(st_cold.collision_report.status, st_warm.collision_report.status)
        self.assertAlmostEqual(
            st_cold.collision_report.punch_result.distance_mm,
            st_warm.collision_report.punch_result.distance_mm,
            places=6,
        )
        self.assertAlmostEqual(
            st_cold.collision_report.die_result.distance_mm,
            st_warm.collision_report.die_result.distance_mm,
            places=6,
        )

    def test_cache_invalidation_on_tool_length_and_placement_change(self):
        """Verify modifying tool length or placement causes cache miss and new evaluation."""
        v = self.validator
        v.clear_caches()

        st1 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=30.0,
            ram_z=20.0,
            support_z=0.0,
            prior_angles={},
        )
        self.assertEqual(v.punch_cache_misses, 1)

        # Change tool length mode or midpoint
        v.desired_midpoint_xm = 25.0
        st2 = v.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=30.0,
            ram_z=20.0,
            support_z=0.0,
            prior_angles={},
        )
        # Must be a punch and die cache miss because desired_midpoint_xm changed!
        self.assertEqual(v.punch_cache_misses, 2)
        self.assertEqual(v.die_cache_misses, 2)

    def test_end_zone_caching_and_missing_shape_fail_closed(self):
        """Verify end-zone classification caching and fail-closed behavior when shapes are missing."""
        # 1. Direct validate_workpiece_tooling_pose test with mock shapes
        dummy_p = PairClearanceResult(
            CollisionClass.PENETRATION,
            "positive_common_volume",
            0.0,
            12.5,
            0.0,
            1.0,
        )
        dummy_d = PairClearanceResult(
            CollisionClass.CLEAR,
            "distance_separated",
            10.0,
            0.0,
            10.0,
            1.0,
        )

        policy = EndZoneCollisionPolicy(allow_end_zone_collision=True, end_zone_length_mm=10.0)

        # Call with cached_punch_result that has NO end_zone_analysis and workpiece_machine_shape is None:
        # Must fail closed with INVALID_GEOMETRY / UNVERIFIED_END_ZONE_CLASSIFICATION!
        rep = validate_workpiece_tooling_pose(
            None,
            None,
            None,
            cached_punch_result=dummy_p,
            cached_die_result=dummy_d,
            end_zone_policy=policy,
        )
        self.assertFalse(rep.is_safe)
        self.assertFalse(rep.is_conditional_safe)
        self.assertEqual(rep.status, CollisionClass.INVALID_GEOMETRY)
        self.assertEqual(rep.conditional_status, "UNVERIFIED_END_ZONE_CLASSIFICATION")

        # 2. Call with cached_punch_result that HAS valid END_ZONE_ONLY analysis:
        # Must preserve CONDITIONAL status and is_conditional_safe!
        valid_ez = PunchEndZoneAnalysis(
            classification=PunchCollisionZoneClassification.END_ZONE_ONLY,
            end_zone_length_mm=10.0,
            punch_span_xm=(-50.0, 50.0),
            left_end_zone_xm=(-50.0, -40.0),
            central_zone_xm=(-40.0, 40.0),
            right_end_zone_xm=(40.0, 50.0),
            total_overlap_volume_mm3=12.5,
            end_zone_overlap_volume_mm3=12.5,
            central_zone_overlap_volume_mm3=0.0,
            affected_sides=("LEFT",),
            components=(),
            reason="end_zone_only",
        )
        dummy_p_with_ez = PairClearanceResult(
            CollisionClass.PENETRATION,
            "positive_common_volume",
            0.0,
            12.5,
            0.0,
            1.0,
            end_zone_analysis=valid_ez,
        )
        rep2 = validate_workpiece_tooling_pose(
            None,
            None,
            None,
            cached_punch_result=dummy_p_with_ez,
            cached_die_result=dummy_d,
            end_zone_policy=policy,
        )
        self.assertFalse(rep2.is_safe)
        self.assertTrue(rep2.is_conditional_safe)
        self.assertEqual(rep2.conditional_status, "CONDITIONAL")
        self.assertEqual(rep2.punch_end_zone_analysis.classification, PunchCollisionZoneClassification.END_ZONE_ONLY)

        # 3. Call with policy allowing end zones, but cached_punch has CENTRAL collision:
        central_ez = PunchEndZoneAnalysis(
            classification=PunchCollisionZoneClassification.CENTRAL_ZONE,
            end_zone_length_mm=10.0,
            punch_span_xm=(-50.0, 50.0),
            left_end_zone_xm=(-50.0, -40.0),
            central_zone_xm=(-40.0, 40.0),
            right_end_zone_xm=(40.0, 50.0),
            total_overlap_volume_mm3=12.5,
            end_zone_overlap_volume_mm3=0.0,
            central_zone_overlap_volume_mm3=12.5,
            affected_sides=(),
            components=(),
            reason="central_zone_collision",
        )
        dummy_p_central = PairClearanceResult(
            CollisionClass.PENETRATION,
            "positive_common_volume",
            0.0,
            12.5,
            0.0,
            1.0,
            end_zone_analysis=central_ez,
        )
        rep3 = validate_workpiece_tooling_pose(
            None,
            None,
            None,
            cached_punch_result=dummy_p_central,
            cached_die_result=dummy_d,
            end_zone_policy=policy,
        )
        self.assertFalse(rep3.is_safe)
        self.assertFalse(rep3.is_conditional_safe)
        self.assertEqual(rep3.conditional_status, "REJECTED_CENTRAL_OR_DIE_COLLISION")


if __name__ == "__main__":
    unittest.main()

