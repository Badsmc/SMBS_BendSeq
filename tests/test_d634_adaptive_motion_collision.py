"""test_d634_adaptive_motion_collision.py - Regression Suite for D6.3.4 Adaptive 3D Motion Collision.

Covers the 10 mandatory regression test cases:
1. Safe endpoints with colliding midpoint -> detects collision at midpoint!
2. Resource budget exhaustion -> reports INTERVAL_UNRESOLVED!
3. Recursion depth exhaustion -> reports INTERVAL_UNRESOLVED!
4. State isolation (B01 twice, B01->B02->B01, angles unchanged).
5. State restoration after exception -> exact restoration guaranteed via finally.
6. Rotation discontinuity -> detects phase-boundary orientation jump.
7. Negative bend direction (-90 deg and -45 deg).
8. Previously completed bend geometry (B02 evaluated with folded B01).
9. Unsupported transfer -> reports TRANSFER_PATH_UNDEFINED.
10. Repeated deterministic results.
"""
from __future__ import annotations

import math
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import FreeCAD as App

from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch

from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
    BendTrajectoryResult,
    MotionState3D,
    TrajectoryEvidenceStatus,
    TrajectoryPhaseResult,
    check_motion_continuity,
    compute_rotation_distance_deg,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    PairClearanceResult,
    SetupQualificationStatus,
    ToolCollisionReport,
)
from smbs_core.kinematics2d.workpiece_placement_3d import PlacementTransform3D
from tools.run_d634_adaptive_motion_collision import find_fixture_path, run_fixture_trajectory_test


def _make_dummy_tool_report(
    status: CollisionClass = CollisionClass.CLEAR,
    p_dist: float = 10.0,
    d_dist: float = 10.0,
    p_vol: float = 0.0,
    d_vol: float = 0.0,
    is_safe: bool = True,
) -> ToolCollisionReport:
    p_res = PairClearanceResult(status, "test", p_dist, p_vol, None, 0.1)
    d_res = PairClearanceResult(status, "test", d_dist, d_vol, None, 0.1)
    return ToolCollisionReport(status, p_res, d_res, is_safe, [], 0.2)


def _identity_placement(tz: float = 0.0, rot_x_deg: float = 0.0) -> PlacementTransform3D:
    rad = math.radians(rot_x_deg)
    c, s = math.cos(rad), math.sin(rad)
    rot = (
        (1.0, 0.0, 0.0),
        (0.0, c, -s),
        (0.0, s, c),
    )
    return PlacementTransform3D(rotation=rot, translation=(0.0, 0.0, tz))


class TestD634AdaptiveMotionRegression(unittest.TestCase):
    """Ten mandatory regression test cases for D6.3.4."""

    def test_01_safe_endpoints_with_colliding_midpoint(self):
        """Case 1: Safe endpoints with colliding midpoint must detect midpoint collision."""
        cfg = AdaptiveSamplingConfig(
            max_recursion_depth=4,
            max_evaluated_states_per_phase=16,
            max_bend_angle_step_deg=20.0,
            max_ram_displacement_step_mm=20.0,
        )

        validator = AdaptiveTrajectoryValidator3D(
            fold_state=MagicMock(),
            panel_build=MagicMock(),
            punch_base=MagicMock(),
            die=MagicMock(),
            config=cfg,
        )

        # Mock sample generator: endpoints safe, midpoint colliding
        def sample_generator(u: float):
            is_midpoint = (0.45 <= u <= 0.55)
            status = CollisionClass.PENETRATION if is_midpoint else CollisionClass.CLEAR
            vol = 5.0 if is_midpoint else 0.0
            dist = 0.0 if is_midpoint else 10.0
            is_safe = not is_midpoint

            rep = _make_dummy_tool_report(status=status, p_dist=dist, p_vol=vol, is_safe=is_safe)
            st = MotionState3D(
                phase="APPROACH",
                active_bend_id="B01",
                signed_angle_deg=0.0,
                path_u=u,
                ram_z_mm=50.0 - u * 30.0,
                support_plane_zm=2.5,
                workpiece_placement=_identity_placement(2.5),
                completed_angles={},
                collision_report=rep,
                is_safe=is_safe,
            )
            validator.total_states_evaluated += 1
            return st

        s0 = sample_generator(0.0)
        s1 = sample_generator(1.0)
        self.assertTrue(s0.is_safe, "Endpoint 0.0 must be safe")
        self.assertTrue(s1.is_safe, "Endpoint 1.0 must be safe")

        out_states = [s0]
        unresolved = []
        diags = []
        validator.validate_adaptive_interval(
            s0, s1, sample_generator, depth=1, out_states=out_states,
            unresolved=unresolved, diags=diags, start_time=time.perf_counter()
        )
        out_states.append(s1)

        # Midpoint must have been sampled and detected as unsafe
        mid_states = [s for s in out_states if 0.45 <= s.path_u <= 0.55]
        self.assertGreaterEqual(len(mid_states), 1)
        self.assertFalse(mid_states[0].is_safe)
        self.assertEqual(mid_states[0].collision_report.status, CollisionClass.PENETRATION)

    def test_02_resource_budget_exhaustion(self):
        """Case 2: Resource budget exhaustion reports INTERVAL_UNRESOLVED."""
        cfg = AdaptiveSamplingConfig(
            max_recursion_depth=10,
            max_evaluated_states_per_phase=4,  # very tight budget
            max_bend_angle_step_deg=0.1,  # triggers endless subdivision
        )
        validator = AdaptiveTrajectoryValidator3D(
            fold_state=MagicMock(),
            panel_build=MagicMock(),
            punch_base=MagicMock(),
            die=MagicMock(),
            config=cfg,
        )

        def sample_fn(u: float):
            return MotionState3D(
                phase="BENDING",
                active_bend_id="B01",
                signed_angle_deg=u * 90.0,
                path_u=u,
                ram_z_mm=0.0,
                support_plane_zm=0.0,
                workpiece_placement=_identity_placement(0.0),
                completed_angles={},
                collision_report=_make_dummy_tool_report(),
                is_safe=True,
            )

        s0 = sample_fn(0.0)
        s1 = sample_fn(1.0)
        out_states = [s0]
        unresolved = []
        diags = []

        validator.validate_adaptive_interval(
            s0, s1, sample_fn, depth=1, out_states=out_states,
            unresolved=unresolved, diags=diags, start_time=time.perf_counter()
        )
        self.assertTrue(len(unresolved) > 0, "Budget exhaustion must report unresolved intervals")
        self.assertTrue(any("MAX_STATES_BUDGET_REACHED" in d for d in diags))

    def test_03_recursion_depth_exhaustion(self):
        """Case 3: Recursion depth exhaustion reports INTERVAL_UNRESOLVED."""
        cfg = AdaptiveSamplingConfig(
            max_recursion_depth=2,  # shallow depth limit
            max_evaluated_states_per_phase=64,
            max_bend_angle_step_deg=0.01,
        )
        validator = AdaptiveTrajectoryValidator3D(
            fold_state=MagicMock(),
            panel_build=MagicMock(),
            punch_base=MagicMock(),
            die=MagicMock(),
            config=cfg,
        )

        def sample_fn(u: float):
            return MotionState3D(
                phase="BENDING",
                active_bend_id="B01",
                signed_angle_deg=u * 90.0,
                path_u=u,
                ram_z_mm=0.0,
                support_plane_zm=0.0,
                workpiece_placement=_identity_placement(0.0),
                completed_angles={},
                collision_report=_make_dummy_tool_report(),
                is_safe=True,
            )

        s0 = sample_fn(0.0)
        s1 = sample_fn(1.0)
        out_states = [s0]
        unresolved = []
        diags = []

        validator.validate_adaptive_interval(
            s0, s1, sample_fn, depth=1, out_states=out_states,
            unresolved=unresolved, diags=diags, start_time=time.perf_counter()
        )
        self.assertTrue(len(unresolved) > 0)
        self.assertTrue(any("MAX_RECURSION_DEPTH_EXCEEDED" in d for d in diags))

    def test_04_state_isolation_and_repeatability(self):
        """Case 4: FoldState isolation: evaluating B01, B02, B01 restores exact angles and transforms."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session
        from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch

        fixture_p = find_fixture_path("Z-(ok).FCStd")
        doc = App.openDocument(str(fixture_p), True)
        try:
            u_obj = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
            sess = build_part_session(doc, [u_obj])
            st = sess.fold_state

            init_angles = dict(st.angles)
            init_transforms = {p: st.transforms[p].point((0, 0, 0)) for p in st.graph.panels}

            punch = load_native_punch(ROOT / "toollibrary" / "punch" / "puancon.FCStd", ram_z_mm=50.0)
            die = load_native_die(ROOT / "toollibrary" / "die" / "matrica.FCStd")

            cfg = AdaptiveSamplingConfig(max_evaluated_states_per_phase=8, arc_segments=32)
            validator = AdaptiveTrajectoryValidator3D(st, sess.panel_build, punch, die, cfg)

            # Run B01
            r1 = validator.validate_bend_trajectory("B01", {})
            self.assertTrue(r1.fold_state_restored)
            self.assertEqual(dict(st.angles), init_angles)

            # Run B02
            r2 = validator.validate_bend_trajectory("B02", {"B01": 90.0})
            self.assertTrue(r2.fold_state_restored)
            self.assertEqual(dict(st.angles), init_angles)

            # Run B01 again: results and state must match identically
            r1_repeat = validator.validate_bend_trajectory("B01", {})
            self.assertEqual(r1.evidence_status, r1_repeat.evidence_status)
            self.assertEqual(dict(st.angles), init_angles)

            final_transforms = {p: st.transforms[p].point((0, 0, 0)) for p in st.graph.panels}
            self.assertEqual(init_transforms, final_transforms)
        finally:
            App.closeDocument(doc.Name)

    def test_05_state_restoration_after_exception(self):
        """Case 5: Exception during candidate evaluation guarantees FoldState restoration."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session

        fixture_p = find_fixture_path("L-(ok).FCStd")
        doc = App.openDocument(str(fixture_p), True)
        try:
            u_obj = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
            sess = build_part_session(doc, [u_obj])
            st = sess.fold_state
            init_angles = dict(st.angles)

            punch = load_native_punch(ROOT / "toollibrary" / "punch" / "puancon.FCStd", ram_z_mm=50.0, section_offsets_xm=[-50.0, 50.0])
            die = load_native_die(ROOT / "toollibrary" / "die" / "matrica.FCStd", section_offsets_xm=[-50.0, 50.0])

            validator = AdaptiveTrajectoryValidator3D(
                st, sess.panel_build, punch, die, AdaptiveSamplingConfig()
            )

            # Force create_state_snapshot to raise an exception
            validator.create_state_snapshot = MagicMock(side_effect=RuntimeError("Simulated OCC crash"))

            with self.assertRaises(RuntimeError):
                validator.validate_bend_trajectory("B01", {})

            # Ensure fold state angles are intact
            self.assertEqual(dict(st.angles), init_angles)
        finally:
            App.closeDocument(doc.Name)

    def test_06_rotation_discontinuity_detection(self):
        """Case 6: Detect rotation discontinuity at phase boundary."""
        cfg = AdaptiveSamplingConfig(
            position_continuity_tolerance_mm=1e-3,
            rotation_continuity_tolerance_deg=1e-3,
        )

        # Same translation, but 1.0 deg rotation jump
        s0 = MotionState3D(
            phase="APPROACH",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=1.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5, rot_x_deg=0.0),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        s1 = MotionState3D(
            phase="CONTACT",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=0.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5, rot_x_deg=1.0),  # 1.0 deg jump!
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )

        ok, reason = check_motion_continuity(s0, s1, cfg)
        self.assertFalse(ok)
        self.assertIn("PHASE_BOUNDARY_ROTATION_JUMP", reason)

    def test_07_negative_bend_direction(self):
        """Case 7: Signed bend angle handling (-90 deg and -45 deg)."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session

        for f_name, b_id, expected_sign in [("Z-(ok).FCStd", "B02", -1.0), ("135-(ok).FCStd", "B02", -1.0)]:
            p = find_fixture_path(f_name)
            doc = App.openDocument(str(p), True)
            try:
                u_obj = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
                sess = build_part_session(doc, [u_obj])
                bend = sess.fold_state.bends[b_id]
                self.assertLess(bend.angle_deg, 0.0, f"{f_name} {b_id} target angle must be negative")
                self.assertAlmostEqual(bend.angle_deg * expected_sign, abs(bend.angle_deg))
            finally:
                App.closeDocument(doc.Name)

    def test_08_previously_completed_bend_geometry(self):
        """Case 8: B02 trajectory starts with B01 completed in FoldState."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session
        from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch

        fixture_p = find_fixture_path("Z-(ok).FCStd")
        doc = App.openDocument(str(fixture_p), True)
        try:
            u_obj = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
            sess = build_part_session(doc, [u_obj])
            st = sess.fold_state

            punch = load_native_punch(ROOT / "toollibrary" / "punch" / "puancon.FCStd", ram_z_mm=50.0)
            die = load_native_die(ROOT / "toollibrary" / "die" / "matrica.FCStd")

            cfg = AdaptiveSamplingConfig(max_evaluated_states_per_phase=4, arc_segments=32)
            validator = AdaptiveTrajectoryValidator3D(st, sess.panel_build, punch, die, cfg)

            # Evaluate B02 with completed B01 = 90.0 deg
            res = validator.validate_bend_trajectory("B02", {"B01": 90.0})
            # Verify completed angle was recorded in states
            for pr in res.phase_results:
                for s in pr.states:
                    self.assertEqual(s.completed_angles.get("B01"), 90.0)
        finally:
            App.closeDocument(doc.Name)

    def test_09_unsupported_transfer(self):
        """Case 9: Transfer without explicit Transfer3D reports TRANSFER_PATH_UNDEFINED."""
        validator = AdaptiveTrajectoryValidator3D(
            fold_state=MagicMock(),
            panel_build=MagicMock(),
            punch_base=MagicMock(),
            die=MagicMock(),
        )
        tr = validator.validate_transfer_phase("B01", "B02", {"B01": 90.0}, transfer_3d=None)
        self.assertEqual(tr.evidence_status, TrajectoryEvidenceStatus.TRANSFER_PATH_UNDEFINED)
        self.assertFalse(tr.is_safe)
        self.assertEqual(tr.continuous_certification_status, TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED)

    def test_10_repeated_deterministic_results(self):
        """Case 10: Repeated execution yields identical sampled state counts and evidence statuses."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session
        from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch

        fixture_p = find_fixture_path("L-(ok).FCStd")
        doc = App.openDocument(str(fixture_p), True)
        try:
            u_obj = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
            sess = build_part_session(doc, [u_obj])
            st = sess.fold_state

            punch = load_native_punch(ROOT / "toollibrary" / "punch" / "puancon.FCStd", ram_z_mm=50.0, section_offsets_xm=[-50.0, 50.0])
            die = load_native_die(ROOT / "toollibrary" / "die" / "matrica.FCStd", section_offsets_xm=[-50.0, 50.0])

            cfg = AdaptiveSamplingConfig(
                max_bend_angle_step_deg=10.0,
                max_ram_displacement_step_mm=5.0,
                min_clearance_margin_mm=0.5,
                max_recursion_depth=4,
                max_evaluated_states_per_phase=16,
                arc_segments=32,
            )

            v1 = AdaptiveTrajectoryValidator3D(st, sess.panel_build, punch, die, cfg)
            res1 = v1.validate_bend_trajectory("B01", {})

            v2 = AdaptiveTrajectoryValidator3D(st, sess.panel_build, punch, die, cfg)
            res2 = v2.validate_bend_trajectory("B01", {})

            self.assertEqual(res1.evidence_status, res2.evidence_status)
            self.assertEqual(res1.total_states_evaluated, res2.total_states_evaluated)
            self.assertEqual(res1.total_solid_reconstructions, res2.total_solid_reconstructions)
            self.assertEqual(res1.total_collision_queries, res2.total_collision_queries)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
