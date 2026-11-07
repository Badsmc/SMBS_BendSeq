"""Integration test verifying D1/D2 contract on real SheetMetal part B01-B08.

Validates that:
1. Real SheetMetal part B01-B08 passes B01 -> B02 transition via active PressCycleController.
2. During TRANSFER, all 52 vertices across all 9 panels are tracked continuously.
3. Angles of previously folded bends (B01 at +90.0 deg) remain strictly invariant.
4. Initial frame (t=0) of Transfer3D exactly equals final placement of B01.
5. Final frame (t=duration) of Transfer3D exactly equals initial setup placement of B02.
6. Euclidean distance is preserved (rigid isometry) across all 52 material points.
7. Pause, Resume, and Reset behave deterministically during TRANSFER phase.
"""
from __future__ import annotations

import math
import unittest

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session
from unittest.mock import patch
from smbs_core.kinematics2d.fold_sequence import PlaybackStatus
from smbs_core.kinematics2d.press_cycle import CyclePhase, PressCycleController, PressCycleError
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.workpiece_placement_validation_3d import validate_rigid_3d

try:
    import FreeCAD
    HAS_FREECAD = True
except ImportError:
    FreeCAD = None
    HAS_FREECAD = False

MODEL_PATH = demo_part_path("box.100out-50in-(ok).FCStd", required=False)


class TestWorkpieceTransfer3DIntegration(unittest.TestCase):
    def setUp(self):
        if not HAS_FREECAD:
            self.skipTest("FreeCAD runtime is required")
        if not MODEL_PATH.is_file():
            self.skipTest(f"Model file not found: {MODEL_PATH}")
        self.doc = FreeCAD.openDocument(str(MODEL_PATH), True)
        obj = next(o for o in self.doc.Objects if o.Name == "Body_Unfold")
        self.session = build_part_session(self.doc, [obj])
        self.fold_state = self.session.fold_state
        self.panel_verts = self.session.panel_vertices_xy
        self.tooling = ToolingSetup2D()

        self.cycle = PressCycleController(
            sequence_controller=self.session.controller,
            tooling_setup=self.tooling,
            animate_workpiece_transfer=True,
        )

    def tearDown(self):
        if hasattr(self, "doc"):
            FreeCAD.closeDocument(self.doc.Name)

    def _get_all_panel_points_machine(self, placement):
        mach_pts = []
        for pid in sorted(self.fold_state.graph.panels):
            p_tf = self.fold_state.transforms[pid]
            for xy in self.panel_verts[pid]:
                p_world = p_tf.point((xy[0], xy[1], 0.0))
                mach_pts.append(placement.apply(p_world))
        return mach_pts

    def test_b01_to_b02_transfer_via_press_cycle_controller(self):
        cycle = self.cycle
        fold_state = self.fold_state

        # 1. Start full cycle playback
        cycle.play_all()
        self.assertEqual(cycle.status, PlaybackStatus.PLAYING)
        self.assertEqual(cycle.active_bend_id, "B01")

        # 2. Advance time until B01 completes and enters TRANSFER phase
        steps = 0
        while cycle.phase != CyclePhase.TRANSFER and steps < 200:
            cycle.advance_time(0.05)
            steps += 1

        self.assertEqual(cycle.phase, CyclePhase.TRANSFER)
        self.assertEqual(cycle.active_bend_id, "B01")
        self.assertAlmostEqual(fold_state.angles["B01"], 90.0, places=5)
        self.assertAlmostEqual(fold_state.angles["B02"], 0.0, places=5)
        self.assertIsNotNone(cycle.transfer_3d)

        # 3. Verify initial placement matches B01 finished pose exactly
        p_b01_final = cycle.current_workpiece_placement_3d()
        validate_rigid_3d(p_b01_final)
        p_transfer_0 = cycle.transfer_3d.start
        validate_rigid_3d(p_transfer_0)

        pts_b01 = self._get_all_panel_points_machine(p_b01_final)
        pts_tr0 = self._get_all_panel_points_machine(p_transfer_0)
        self.assertEqual(len(pts_b01), 52)
        for p1, p2 in zip(pts_b01, pts_tr0):
            self.assertAlmostEqual(math.dist(p1, p2), 0.0, places=6)

        # 4. Advance through TRANSFER frames: verify continuity, angles preservation and isometry
        prev_pts = pts_tr0
        prev_progress = cycle.transfer_progress
        pA_part = (0.0, 0.0, 0.0)
        pB_part = (100.0, 50.0, 0.0)
        d_initial = math.dist(pA_part, pB_part)

        transfer_ticks = 0
        while cycle.phase == CyclePhase.TRANSFER and transfer_ticks < 200:
            cycle.advance_time(0.05)
            transfer_ticks += 1

            # B01 angle must not change during transfer!
            self.assertAlmostEqual(fold_state.angles["B01"], 90.0, places=6)
            self.assertAlmostEqual(fold_state.angles["B02"], 0.0, places=6)

            if cycle.phase == CyclePhase.TRANSFER:
                self.assertGreaterEqual(cycle.transfer_progress, prev_progress)
                prev_progress = cycle.transfer_progress

                p_curr = cycle.current_workpiece_placement_3d()
                validate_rigid_3d(p_curr)

                curr_pts = self._get_all_panel_points_machine(p_curr)
                self.assertEqual(len(curr_pts), 52)

                # Check max point displacement per tick is bounded
                max_tick_disp = max(math.dist(p1, p2) for p1, p2 in zip(prev_pts, curr_pts))
                self.assertLess(max_tick_disp, 25.0)  # smooth continuous motion

                # Check Euclidean distance invariance
                pA_m = p_curr.apply(pA_part)
                pB_m = p_curr.apply(pB_part)
                self.assertAlmostEqual(math.dist(pA_m, pB_m), d_initial, places=6)

                prev_pts = curr_pts

        # 5. TRANSFER must complete and enter APPROACH for B02
        self.assertGreater(transfer_ticks, 5)
        self.assertEqual(cycle.phase, CyclePhase.APPROACH)
        self.assertEqual(cycle.active_bend_id, "B02")
        self.assertIsNone(cycle.transfer_3d)

        # 6. Verify final position matches B02 initial setup placement exactly
        bz = fold_state.bends["B02"].bend_zone
        outer_r = bz.outer_radius_mm if bz is not None else 0.0
        p_b02_initial = WorkpiecePlacement3D.compute_placement(
            fold_state, "B02", support_plane_zm=outer_r, align_bisector=True, align_bend_arc_center=True
        )
        p_at_b02 = cycle.current_workpiece_placement_3d()
        pts_b02_setup = self._get_all_panel_points_machine(p_b02_initial)
        pts_b02_actual = self._get_all_panel_points_machine(p_at_b02)

        for p1, p2 in zip(pts_b02_setup, pts_b02_actual):
            self.assertAlmostEqual(math.dist(p1, p2), 0.0, places=6)

    def test_pause_resume_and_reset_during_transfer(self):
        cycle = self.cycle
        fold_state = self.fold_state

        cycle.play_all()
        # Advance until TRANSFER
        while cycle.phase != CyclePhase.TRANSFER:
            cycle.advance_time(0.05)

        # Advance partially into TRANSFER
        for _ in range(5):
            cycle.advance_time(0.05)

        self.assertEqual(cycle.phase, CyclePhase.TRANSFER)
        saved_progress = cycle.transfer_progress
        self.assertGreater(saved_progress, 0.0)
        self.assertLess(saved_progress, 1.0)
        pose_before_pause = cycle.current_workpiece_placement_3d()

        # 1. Test Pause
        cycle.pause()
        self.assertEqual(cycle.status, PlaybackStatus.PAUSED)
        self.assertEqual(cycle.phase, CyclePhase.TRANSFER)

        # Advance time while paused: must not advance
        changed = cycle.advance_time(0.05)
        self.assertFalse(changed)
        self.assertEqual(cycle.transfer_progress, saved_progress)
        pose_during_pause = cycle.current_workpiece_placement_3d()
        self.assertEqual(pose_before_pause, pose_during_pause)

        # 2. Test Resume
        cycle.resume()
        self.assertEqual(cycle.status, PlaybackStatus.PLAYING)
        self.assertEqual(cycle.phase, CyclePhase.TRANSFER)

        cycle.advance_time(0.05)
        self.assertGreater(cycle.transfer_progress, saved_progress)

        # 3. Test Reset during TRANSFER
        cycle.reset()
        self.assertEqual(cycle.phase, CyclePhase.IDLE)
        self.assertEqual(cycle.active_bend_id, "B01")
        self.assertEqual(cycle.transfer_progress, 0.0)
        self.assertIsNone(cycle.transfer_3d)
        for b in fold_state.bends:
            self.assertAlmostEqual(fold_state.angles[b], 0.0)

    def test_transfer_3d_failure_causes_deterministic_halt_without_silent_recovery(self):
        cycle = self.cycle
        fold_state = self.fold_state

        cycle.play_all()
        # Advance until STEP_COMPLETE right before TRANSFER
        while cycle.phase != CyclePhase.STEP_COMPLETE:
            cycle.advance_time(0.05)

        self.assertEqual(cycle.phase, CyclePhase.STEP_COMPLETE)
        self.assertAlmostEqual(fold_state.angles["B01"], 90.0)
        self.assertAlmostEqual(fold_state.angles["B02"], 0.0)

        # Force transfer trajectory construction to fail
        with patch("smbs_core.kinematics2d.workpiece_transfer_3d.build_transfer_3d", side_effect=ValueError("Simulated 3D transfer failure")):
            with self.assertRaises(PressCycleError) as ctx:
                cycle.advance_time(0.05)

            self.assertIn("TRANSFER_TRAJECTORY_FAILED", str(ctx.exception))
            self.assertIn("Simulated 3D transfer failure", str(ctx.exception))

        # Check:
        # 1. Playback status is PAUSED, not continuing
        self.assertEqual(cycle.status, PlaybackStatus.PAUSED)
        # 2. Fault is recorded explicitly
        self.assertIsNotNone(cycle.transfer_fault)
        self.assertIn("TRANSFER_TRAJECTORY_FAILED", cycle.transfer_fault)
        # 3. transfer_3d is None
        self.assertIsNone(cycle.transfer_3d)
        # 4. FoldState angles remain strictly unchanged
        self.assertAlmostEqual(fold_state.angles["B01"], 90.0)
        self.assertAlmostEqual(fold_state.angles["B02"], 0.0)
        # 5. Subsequent advance_time returns False and does not advance
        changed = cycle.advance_time(0.05)
        self.assertFalse(changed)
        # 6. Attempting to query workpiece placement raises PressCycleError rather than returning dummy pose
        with self.assertRaises(PressCycleError):
            cycle.current_workpiece_placement_3d()

        # 7. Calling resume() while transfer_fault is set must not resume playback
        cycle.resume()
        self.assertEqual(cycle.status, PlaybackStatus.PAUSED)
        self.assertFalse(cycle.advance_time(0.05))


if __name__ == "__main__":
    unittest.main()

