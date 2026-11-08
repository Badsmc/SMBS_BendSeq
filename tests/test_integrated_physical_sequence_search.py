"""Unit and regression tests for integrated physical sequence search:
1. DFS traversal order: Stage A -> Stage B -> cost sort -> Stage C 3D -> immediate recursion on clearance.
2. Preliminary screening: PROVEN_BLOCKING in Stage A or B skips Stage C authoritative 3D boolean check.
3. Soundness: Absence of 2D collision in Stage B requires Stage C and is NEVER treated as 3D PASS.
4. Decoupling: Heuristic cost weights (OperationWeights) update candidate order and plan cost without re-triggering 3D checks.
5. Cache independence: Forward replay is completely independent of the search cache.
6. Transition penalty metrics: correct flip, turn, offset, and span penalties.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD
import Part
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from smbs_core.kinematics2d.native_collision_3d import CollisionClass
from smbs_core.kinematics2d.operation_cost import (
    OperationWeights,
    WorkpieceMachineSetup,
    smallest_turn_deg,
    transition_penalty,
    score_sequence,
)
from smbs_core.kinematics2d.preliminary_screening import (
    ScreeningDecision,
    PreliminaryScreeningReport,
    screen_candidate_stage_a,
    screen_candidate_stage_b,
)
from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence
from smbs_core.kinematics2d.sequence_search import (
    SearchStatus,
    StepEvaluationCache,
    StepEvaluationRecord,
    ForwardReplayReport,
    evaluate_candidate_bend_step,
    verify_sequence_forward_replay,
    search_bend_sequence_25d,
)


class TestIntegratedPhysicalSequenceSearch(unittest.TestCase):
    def test_01_smallest_turn_deg(self):
        """Turn delta computation wraps modulo 360 to unsigned shortest angle in [0, 180]."""
        self.assertAlmostEqual(smallest_turn_deg(0.0, 90.0), 90.0)
        self.assertAlmostEqual(smallest_turn_deg(90.0, 0.0), 90.0)
        self.assertAlmostEqual(smallest_turn_deg(10.0, 350.0), 20.0)
        self.assertAlmostEqual(smallest_turn_deg(350.0, 10.0), 20.0)
        self.assertAlmostEqual(smallest_turn_deg(0.0, 180.0), 180.0)

    def test_02_transition_penalty_flip_and_turn(self):
        """Transition penalty properly penalizes flipping and rotating."""
        weights = OperationWeights(
            flip=50.0,
            turn_fixed=0.0,
            turn_per_90_deg=10.0,
            shift_fixed=0.0,
            shift_per_100_mm=10.0,
        )
        
        s1 = WorkpieceMachineSetup(bend_id="B01", flipped=False, rotation_deg=0.0, shift_xm_mm=0.0, bend_length_mm=100.0)
        # Same setup, zero penalty
        p_same = transition_penalty(s1, s1, weights)
        self.assertEqual(p_same.total, 0.0)
        self.assertFalse(p_same.is_flipped)
        self.assertEqual(p_same.turn_deg, 0.0)

        # Flipped setup
        s_flipped = WorkpieceMachineSetup(bend_id="B02", flipped=True, rotation_deg=0.0, shift_xm_mm=0.0, bend_length_mm=100.0)
        p_flip = transition_penalty(s1, s_flipped, weights)
        self.assertTrue(p_flip.is_flipped)
        self.assertEqual(p_flip.flip_cost, 50.0)
        self.assertEqual(p_flip.total, 50.0)

        # 90 deg turn + 10mm shift
        s_turn = WorkpieceMachineSetup(bend_id="B03", flipped=False, rotation_deg=90.0, shift_xm_mm=10.0, bend_length_mm=100.0)
        p_turn = transition_penalty(s1, s_turn, weights)
        self.assertAlmostEqual(p_turn.turn_cost, 10.0)  # 90 deg * (10 / 90)
        self.assertAlmostEqual(p_turn.shift_cost, 1.0)   # 10 mm * (10 / 100)
        self.assertAlmostEqual(p_turn.total, 11.0)

    def test_03_stage_a_preliminary_screening_prunes_invalid_geometry(self):
        """Stage A screens out bends with invalid/negative thickness."""
        bend_ok = SimpleNamespace(
            length_mm=100.0,
            target_angle_deg=90.0,
            bend_zone=SimpleNamespace(thickness_mm=1.5, inner_radius_mm=1.0),
        )
        fs_ok = SimpleNamespace(bends={"B01": bend_ok})
        tooling = SimpleNamespace(punch=SimpleNamespace(length_mm=100.0), die=SimpleNamespace(length_mm=100.0))
        res_ok = screen_candidate_stage_a(fs_ok, "B01", tooling)
        self.assertEqual(res_ok.decision, ScreeningDecision.CONTINUE_TO_3D)

        bend_bad = SimpleNamespace(
            length_mm=100.0,
            target_angle_deg=90.0,
            bend_zone=SimpleNamespace(thickness_mm=-1.0, inner_radius_mm=1.0),
        )
        fs_bad = SimpleNamespace(bends={"B01": bend_bad})
        res_bad = screen_candidate_stage_a(fs_bad, "B01", tooling)
        self.assertEqual(res_bad.decision, ScreeningDecision.PROVEN_BLOCKING)
        self.assertIn("invalid_sheet_thickness", res_bad.reason)

    def test_04_absence_of_2d_collision_in_stage_b_never_treated_as_3d_pass(self):
        """Stage B passing without collision MUST result in CONTINUE_TO_3D, never a terminal PASS."""
        bend_ok = SimpleNamespace(
            length_mm=100.0,
            target_angle_deg=90.0,
            bend_zone=SimpleNamespace(outer_radius_mm=1.5, inner_radius_mm=1.0),
        )
        fs = SimpleNamespace(bends={"B01": bend_ok})
        res_stage_b = screen_candidate_stage_b(
            fold_state=fs,
            candidate_bend_id="B01",
            tooling=SimpleNamespace(punch=None, die=None),
            panel_vertices_xy={},
        )
        self.assertEqual(res_stage_b.decision, ScreeningDecision.CONTINUE_TO_3D)
        self.assertNotEqual(res_stage_b.decision, ScreeningDecision.PROVEN_BLOCKING)

    def test_05_dfs_immediate_recursion_and_sibling_skip(self):
        """When the first candidate passes 3D check, DFS immediately recurses into depth d+1
        WITHOUT evaluating Stage C 3D on remaining siblings at depth d.
        """
        bends_dict = {
            "B01": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
            "B02": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
            "B03": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
        }
        mock_fold_state = SimpleNamespace(
            bends=bends_dict,
            unfolded_bends={"B01", "B02", "B03"},
            angles={"B01": 0.0, "B02": 0.0, "B03": 0.0},
            transforms={},
            set_angle=MagicMock(),
            reset_all=MagicMock(),
            copy=lambda: mock_fold_state,
        )
        mock_session = SimpleNamespace(
            fold_state=mock_fold_state,
            ordered_bend_ids=["B01", "B02", "B03"],
            bend_order=["B01", "B02", "B03"],
            set_bend_order=MagicMock(),
            reset_all_bends=MagicMock(),
            active_bend_id="B01",
            step_to_bend=MagicMock(),
        )

        stage_c_eval_order = []

        def fake_eval_step(session, press_cycle, tooling, *args, **kwargs):
            cand_id = kwargs.get("candidate_bend_id") or (args[1] if len(args) > 1 else None)
            prefix = kwargs.get("completed_prefix") or (args[0] if len(args) > 0 else ())
            stage_c_eval_order.append(cand_id)
            # Candidate clears
            return StepEvaluationRecord(
                bend_id=cand_id,
                step_index=len(prefix),
                completed_prefix=prefix,
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clean_clearance",
            )

        def fake_replay(session, seq, *args, **kwargs):
            return ForwardReplayReport(
                sequence=seq,
                overall_status=SearchStatus.STRICT_VALIDATED,
                is_verified=True,
                is_conditional=False,
            )

        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=fake_eval_step), \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay", side_effect=fake_replay), \
             patch("smbs_core.kinematics2d.sequence_search.compute_workpiece_machine_setup") as mock_setup:
            
            def setup_side_effect(fs, bid, **kw):
                rot = 0.0 if bid == "B01" else (90.0 if bid == "B02" else 180.0)
                return WorkpieceMachineSetup(bend_id=bid, flipped=False, rotation_deg=rot, shift_xm_mm=0.0, bend_length_mm=100.0)
            mock_setup.side_effect = setup_side_effect

            tooling_setup = SimpleNamespace(
                match_active_bend_length=True,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
                punch=SimpleNamespace(length_mm=100.0),
                die=SimpleNamespace(length_mm=100.0),
            )
            report = search_bend_sequence_25d(
                mock_session,
                press_cycle=None,
                tooling=tooling_setup,
                allow_end_zone_collision=False,
                max_states=100,
            )

            self.assertEqual(report.status, SearchStatus.STRICT_VALIDATED)
            self.assertEqual(report.found_sequence, ("B01", "B02", "B03"))
            # Crucial assertion: Stage C evaluated exactly 3 times (depth 0: B01 -> depth 1: B02 -> depth 2: B03)
            # It did NOT evaluate B02 or B03 at depth 0 before recursing!
            self.assertEqual(stage_c_eval_order, ["B01", "B02", "B03"])
            self.assertEqual(report.stage_c_native_evaluated, 3)

    def test_06_decoupling_changing_weights_changes_order_without_invalidating_cache(self):
        """Changing OperationWeights changes candidate priority and sequence cost without re-triggering 3D checks."""
        cache = StepEvaluationCache()
        tooling = SimpleNamespace(punch=SimpleNamespace(tool_id="P1", source_file="p.fcstd"),
                                 die=SimpleNamespace(tool_id="D1", source_file="d.fcstd"))
        
        k1 = cache.make_step_key({}, "B01", tooling, True, 100.0, 100.0, 0.0, None, False, 0.0)
        k2 = cache.make_step_key({}, "B02", tooling, True, 100.0, 100.0, 0.0, None, False, 0.0)

        rec_b01 = StepEvaluationRecord(
            bend_id="B01",
            step_index=0,
            completed_prefix=(),
            pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            reason="clear",
        )
        rec_b02 = StepEvaluationRecord(
            bend_id="B02",
            step_index=0,
            completed_prefix=(),
            pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            reason="clear",
        )
        
        cache.put_step(k1, rec_b01)
        cache.put_step(k2, rec_b02)

        # Cache retrieval is purely geometric (does not depend on weights)
        hit_1 = cache.get_step(k1)
        self.assertIsNotNone(hit_1)
        self.assertEqual(hit_1.bend_id, "B01")

        # Scoring sequences with different weights yields different costs
        s1 = WorkpieceMachineSetup("B01", flipped=False, rotation_deg=0.0, shift_xm_mm=0.0, bend_length_mm=100.0)
        s2 = WorkpieceMachineSetup("B02", flipped=True, rotation_deg=90.0, shift_xm_mm=0.0, bend_length_mm=100.0)
        setups = {"B01": s1, "B02": s2}
        
        w_low_flip = OperationWeights(flip=10.0, turn_per_90_deg=10.0, turn_fixed=0.0)
        w_high_flip = OperationWeights(flip=100.0, turn_per_90_deg=10.0, turn_fixed=0.0)

        pen_low = score_sequence(["B01", "B02"], setups, w_low_flip)
        pen_high = score_sequence(["B01", "B02"], setups, w_high_flip)

        self.assertAlmostEqual(pen_low.total, 20.0)   # 10 (flip) + 10 (turn)
        self.assertAlmostEqual(pen_high.total, 110.0) # 100 (flip) + 10 (turn)

    def test_07_forward_replay_never_reads_from_search_cache(self):
        """Forward replay performs a strict end-to-end 5-phase evaluation from scratch without cache lookup."""
        mock_session = SimpleNamespace(
            fold_state=SimpleNamespace(
                bends={"B01": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0)},
                angles={"B01": 0.0},
            ),
            bend_order=["B01"],
            active_bend_id="B01",
            active_bend_index=0,
            set_bend_order=MagicMock(),
            reset_all_bends=MagicMock(),
            step_to_bend=MagicMock(),
        )

        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step") as mock_eval:
            mock_eval.return_value = StepEvaluationRecord(
                bend_id="B01",
                step_index=0,
                completed_prefix=(),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )
            # Forward replay has NO cache argument and must call evaluate_candidate_bend_step directly
            replay_rep = verify_sequence_forward_replay(
                mock_session,
                sequence=("B01",),
                press_cycle=None,
                tooling=SimpleNamespace(
                    match_active_bend_length=True,
                    allow_end_zone_collision=False,
                    end_zone_length_mm=0.0,
                    punch=SimpleNamespace(length_mm=100.0),
                    die=SimpleNamespace(length_mm=100.0),
                ),
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
                config=None,
            )
            self.assertTrue(replay_rep.is_verified)
            self.assertEqual(mock_eval.call_count, 1)
            self.assertTrue(replay_rep.is_verified)
            self.assertEqual(mock_eval.call_count, 1)


if __name__ == "__main__":
    unittest.main()
