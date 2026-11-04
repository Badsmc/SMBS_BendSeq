"""Comprehensive regression suite for Stage D4-P2: Motion Continuity Validation."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.press_cycle import (
    PressCycleController,
    CycleExecutionMode,
    CyclePhase,
    CycleConfig,
    PlaybackStatus,
)
from smbs_core.kinematics2d.workpiece_placement_3d import (
    WorkpiecePlacement3D,
    PlacementTransform3D,
)
from smbs_core.kinematics2d.workpiece_transfer_3d import build_transfer_3d
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D
from smbs_core.kinematics2d.fold_sequence import BendSequenceController
from smbs_core.kinematics2d.motion_trace import (
    extract_physical_landmarks,
    make_frame_record,
    MotionTraceRecorder,
)
from smbs_core.kinematics2d.trace_analyzer import analyze_trace, TraceAnalysisReport


class MockSequence:
    def __init__(self, fold_state: FoldState, order: list[str]):
        self.fold_state = fold_state
        self.order = tuple(order)
        self.active_index = 0
        self.total_steps = len(order)
        self.status = PlaybackStatus.IDLE

    @property
    def active_bend(self):
        bid = self.order[self.active_index]
        return self.bend_for(bid)

    def bend_for(self, bend_id: str):
        bend = self.fold_state.bends[bend_id]
        class BendObj:
            def __init__(self, bid, target_angle, zone):
                self.bend_id = bid
                self.angle_deg = target_angle
                self.bend_zone = zone
        return BendObj(bend_id, bend.angle_deg, bend.bend_zone)


def make_three_panel_graph_state():
    p1 = bend_parameters(90.0, 1.0, 1.5, 0.4)
    p2 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
    z1 = make_bend_zone("B01", ((0.0, 50.0), (100.0, 50.0)), p1)
    z2 = make_bend_zone("B02", ((50.0, 50.0), (50.0, 150.0)), p2)

    graph = PanelGraph(
        ("P00", "P01", "P02"),
        (
            GraphEdge("B01", "P00", "P01", 90.0, ((0.0, 50.0, 0.0), (100.0, 50.0, 0.0)), z1),
            GraphEdge("B02", "P00", "P02", -90.0, ((50.0, 50.0, 0.0), (50.0, 150.0, 0.0)), z2),
        ),
    )
    fold_state = FoldState(graph, "P00")
    panel_verts = {
        "P00": ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)),
        "P01": ((0.0, 52.0), (100.0, 52.0), (100.0, 100.0), (0.0, 100.0)),
        "P02": ((52.0, 50.0), (100.0, 50.0), (100.0, 150.0), (52.0, 150.0)),
    }
    return fold_state, panel_verts


class MotionContinuityD4P2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_physical_landmark_extraction_authoritative(self):
        """Physical landmarks must produce persistent panel vertex IDs and bend axis coordinates."""
        fs, panel_verts = make_three_panel_graph_state()
        seq = BendSequenceController(fs)
        ctrl = PressCycleController(seq, ToolingSetup2D())

        class MockSession:
            def __init__(self, sequence, verts):
                self.sequence = sequence
                self.panel_vertices_xy = verts

        sess = MockSession(seq, panel_verts)
        lms = extract_physical_landmarks(sess, ctrl)

        # Must contain all panel vertices (4 vertices * 3 panels = 12)
        for p in ("P00", "P01", "P02"):
            for v in range(4):
                k = f"panel:{p}:v{v}"
                self.assertIn(k, lms)
                self.assertEqual(len(lms[k]), 3)

        # Must contain bend axes
        for b in ("B01", "B02"):
            for suff in ("start", "end", "mid"):
                self.assertIn(f"bend:{b}:{suff}", lms)

    def test_placement_continuity_opposite_bend_directions(self):
        """Opposite bend directions (+90 deg and -90 deg) must maintain continuous placement."""
        fs, _ = make_three_panel_graph_state()
        p_b01 = WorkpiecePlacement3D.compute_placement(fs, "B01", align_bisector=False)
        p_b01_bi = WorkpiecePlacement3D.compute_placement(fs, "B01", align_bisector=True)
        # At angle=0, bisector alignment must match flat pose
        for a, b in zip(p_b01.translation, p_b01_bi.translation):
            self.assertAlmostEqual(a, b, places=5)

        p_b02 = WorkpiecePlacement3D.compute_placement(fs, "B02", align_bisector=False)
        p_b02_bi = WorkpiecePlacement3D.compute_placement(fs, "B02", align_bisector=True)
        # For negative bend B02 at angle=0, bisector alignment must also match flat pose
        for a, b in zip(p_b02.translation, p_b02_bi.translation):
            self.assertAlmostEqual(a, b, places=5)

    def test_transfer_trajectory_zero_boundary_jump(self):
        """Transfer3D start and end poses must match post-retract and approach poses within 1e-6 mm."""
        fs, _ = make_three_panel_graph_state()
        seq = BendSequenceController(fs)
        ctrl = PressCycleController(seq, ToolingSetup2D())
        ctrl.execution_mode = CycleExecutionMode.FULL_CYCLE

        # Fold B01 to 90 deg
        fs.set_angle("B01", 90.0)
        ctrl.phase = CyclePhase.RETRACT

        p_retract = ctrl.current_workpiece_placement_3d()
        p_step_complete = p_retract

        ctrl.phase = CyclePhase.STEP_COMPLETE
        # STEP_COMPLETE maintains exact placement
        p_sc = ctrl.current_workpiece_placement_3d()
        self.assertAlmostEqual(math.dist(p_retract.translation, p_sc.translation), 0.0, places=5)

        # End pose of transfer
        nxt_b = seq.bend_for("B02")
        end_pose = WorkpiecePlacement3D.compute_placement(
            fs, nxt_b.bend_id, support_plane_zm=0.0, align_bisector=False
        )

        tf = build_transfer_3d(p_retract, end_pose, max_linear_mm_s=250.0, max_angular_deg_s=90.0)
        p_tf_0 = tf.pose_at(0.0)
        p_tf_1 = tf.pose_at(tf.duration_s)

        self.assertAlmostEqual(math.dist(p_retract.translation, p_tf_0.translation), 0.0, places=5)
        self.assertAlmostEqual(math.dist(end_pose.translation, p_tf_1.translation), 0.0, places=5)

    def test_pause_resume_reset_continuity(self):
        """Pause, resume, and reset operations must not introduce artificial discontinuities."""
        fs, _ = make_three_panel_graph_state()
        seq = BendSequenceController(fs)
        ctrl = PressCycleController(seq, ToolingSetup2D())
        ctrl.execution_mode = CycleExecutionMode.FULL_CYCLE
        ctrl.play_all()

        p_init = ctrl.current_workpiece_placement_3d()
        ctrl.advance_time(0.05)
        p_moving = ctrl.current_workpiece_placement_3d()

        # Pause
        ctrl.pause()
        self.assertEqual(ctrl.sequence.status, PlaybackStatus.PAUSED)
        p_paused = ctrl.current_workpiece_placement_3d()
        self.assertAlmostEqual(math.dist(p_moving.translation, p_paused.translation), 0.0, places=5)

        # Resume
        ctrl.resume()
        self.assertEqual(ctrl.sequence.status, PlaybackStatus.PLAYING)
        p_resumed = ctrl.current_workpiece_placement_3d()
        self.assertAlmostEqual(math.dist(p_paused.translation, p_resumed.translation), 0.0, places=5)

        # Reset
        ctrl.reset()
        p_reset = ctrl.current_workpiece_placement_3d()
        self.assertEqual(ctrl.phase, CyclePhase.IDLE)
        self.assertAlmostEqual(math.dist(p_init.translation, p_reset.translation), 0.0, places=5)

    def test_trace_analyzer_detects_injected_discontinuity(self):
        """TraceAnalyzer must flag unphysical coordinate jumps (> 0.05 mm in stationary phase) as FAIL."""
        recorder = MotionTraceRecorder(directory=self.tmp_dir.name, jump_mm=0.05)
        trace_path = recorder.start()

        # Record normal frames
        lm1 = {"panel:P1:v0": (0.0, 0.0, 0.0), "panel:P1:v1": (10.0, 0.0, 0.0)}
        f1 = {
            "kind": "FRAME",
            "cycle_phase": "BOTTOM",
            "active_bend_id": "B01",
            "elapsed_dt_s": 0.02,
            "physical_landmarks": lm1,
        }
        recorder.record(f1)

        # Injected jump in stationary BOTTOM phase (5.0 mm step)
        lm2 = {"panel:P1:v0": (5.0, 0.0, 0.0), "panel:P1:v1": (15.0, 0.0, 0.0)}
        f2 = {
            "kind": "FRAME",
            "cycle_phase": "BOTTOM",
            "active_bend_id": "B01",
            "elapsed_dt_s": 0.02,
            "physical_landmarks": lm2,
        }
        recorder.record(f2)
        recorder.stop(reason="TEST_FINISH")

        report = analyze_trace(trace_path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertGreaterEqual(report.discontinuities_count, 1)
        self.assertTrue(any("STATIONARY_PHASE_MOTION" in d.reason for d in report.discontinuities))


if __name__ == "__main__":
    unittest.main()
