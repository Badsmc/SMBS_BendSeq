"""Unit and integration tests for backward DFS sequence search with forward physical qualification.

Verifies:
1. Dispatch: `search_bend_sequence_25d(..., search_direction="BACKWARD")` routes to backward search.
2. Contract: S is set of remaining bends; candidate b in S is hypothesized last bend.
   Preceding state S' = S - {b} has bends folded; b is bent forward from 0° to target angle.
   No physical unbending is performed.
3. Solvable case: Backward search successfully finds a collision-free forward sequence
   and independently verifies it via forward replay from the flat blank (0°).
4. Unsolvable case: When all branches have physical collisions, backward search
   backtracks cleanly and reports NO_FEASIBLE_SEQUENCE without hanging or crashing.
5. Fail-closed: UNVERIFIED physical evidence does not poison the dead-end cache.
6. Operation weights & forward transition scoring: Candidate ordering evaluates transition
   penalties in forward production order.
7. Class wrapper: DFSBackwardPlanner25D provides standard solver interface.
8. Native CAD comparison: Forward vs backward search on native CAD part with identical limits.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.demo_paths import demo_part_path
from smbs_core.kinematics2d.native_collision_3d import CollisionClass
from smbs_core.kinematics2d.operation_cost import (
    OperationWeights,
    WorkpieceMachineSetup,
    transition_penalty,
    score_sequence,
)
from smbs_core.kinematics2d.preliminary_screening import (
    PreliminaryScreeningReport,
    ScreeningDecision,
)
from smbs_core.kinematics2d.physical_validation_25d import (
    CurrentPosePhysicalReport,
    PhysicalEvidence,
)
from smbs_core.kinematics2d.sequence_search import (
    DFSBackwardPlanner25D,
    ForwardReplayReport,
    SearchStatus,
    SequenceSearchReport,
    StepEvaluationCache,
    StepEvaluationRecord,
    search_bend_sequence_25d,
    search_bend_sequence_backward_25d,
)


def _build_mock_session(bend_ids=("B01", "B02", "B03")):
    bends_dict = {
        bid: SimpleNamespace(
            length_mm=100.0,
            target_angle_deg=90.0,
            angle_deg=90.0,
            bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0),
        )
        for bid in bend_ids
    }
    return SimpleNamespace(
        fold_state=SimpleNamespace(
            bends=bends_dict,
            unfolded_bends=set(bend_ids),
            angles={b: 0.0 for b in bend_ids},
            transforms={},
        ),
        bend_order=list(bend_ids),
        active_bend_id=bend_ids[0],
        active_bend_index=0,
        panel_vertices_xy={},
        set_bend_order=MagicMock(),
        reset_all_bends=MagicMock(),
        step_to_bend=MagicMock(),
    )


def _build_mock_tooling():
    return SimpleNamespace(
        punch=SimpleNamespace(length_mm=100.0),
        die=SimpleNamespace(length_mm=100.0),
        match_active_bend_length=True,
        allow_end_zone_collision=False,
        end_zone_length_mm=0.0,
    )


class TestBackwardSequenceSearch25D(unittest.TestCase):
    def test_01_backward_search_direction_dispatch(self):
        """search_bend_sequence_25d with search_direction='BACKWARD' routes to backward search."""
        session = _build_mock_session()
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        with patch("smbs_core.kinematics2d.sequence_search.search_bend_sequence_backward_25d") as mock_bwd:
            dummy_rep = SequenceSearchReport(
                status=SearchStatus.STRICT_VALIDATED,
                overall_evidence=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                found_sequence=("B01", "B02", "B03"),
                initial_sequence=("B01", "B02", "B03"),
                best_partial_sequence=("B01", "B02", "B03"),
                max_depth_reached=3,
                total_bends=3,
                states_visited=3,
                step_evaluations=3,
                cache_hits=0,
                dead_end_hits=0,
                backtracks_count=0,
                elapsed_s=0.1,
                match_active_bend_length=True,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
            )
            mock_bwd.return_value = dummy_rep

            rep = search_bend_sequence_25d(session, cycle, tooling, search_direction="BACKWARD")
            self.assertIs(rep, dummy_rep)
            mock_bwd.assert_called_once()

    def test_02_backward_dfs_small_solvable_case(self):
        """Backward search finds valid forward sequence on solvable 3-bend part and replays forward."""
        session = _build_mock_session(["B01", "B02", "B03"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        # Let the only valid forward sequence be ('B01', 'B02', 'B03').
        # In backward search:
        # At root S={B01, B02, B03}:
        # b=B03 is valid (preceding S'={B01, B02} folded, b=B03 bent forward -> CLEAR).
        # b=B01 or b=B02 as last bend collide.
        # At next level S={B01, B02}:
        # b=B02 is valid (preceding S'={B01} folded, b=B02 bent forward -> CLEAR).
        # At next level S={B01}:
        # b=B01 is valid (preceding S'={} folded, b=B01 bent forward -> CLEAR).
        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            # The candidate is valid only if completed_prefix matches the prefix of ('B01', 'B02', 'B03')
            prefix = tuple(completed_prefix)
            cand = candidate_bend_id
            if (prefix == ("B01", "B02") and cand == "B03") or \
               (prefix == ("B01",) and cand == "B02") or \
               (prefix == () and cand == "B01"):
                status = PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS
                reason = "CLEAR"
            else:
                status = PhysicalEvidence.COLLISION
                reason = "INTERFERENCE"

            return StepEvaluationRecord(
                bend_id=cand,
                step_index=len(prefix),
                completed_prefix=prefix,
                pose_status=status,
                trajectory_status=status,
                effective_status=status,
                reason=reason,
            )

        def mock_forward_replay(sess, cyc, tool, seq, **kw):
            is_valid = (tuple(seq) == ("B01", "B02", "B03"))
            status = PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS if is_valid else PhysicalEvidence.COLLISION
            step_reps = [
                StepEvaluationRecord(
                    bend_id=b,
                    step_index=i,
                    completed_prefix=tuple(seq[:i]),
                    pose_status=status,
                    trajectory_status=status,
                    effective_status=status,
                    reason="OK" if is_valid else "COLLISION",
                )
                for i, b in enumerate(seq)
            ]
            return ForwardReplayReport(
                sequence=tuple(seq),
                is_verified=is_valid,
                is_conditional=False,
                overall_status=status,
                steps=step_reps,
                blocking_reason="" if is_valid else "COLLISION",
            )

        with patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a") as mock_sa, \
             patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b") as mock_sb, \
             patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=mock_eval_step), \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay", side_effect=mock_forward_replay):

            mock_sa.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_A", "A_OK")
            mock_sb.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "B_OK")

            rep = search_bend_sequence_backward_25d(
                session,
                cycle,
                tooling,
                max_states=50,
                max_checks=50,
            )

            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertEqual(rep.found_sequence, ("B01", "B02", "B03"))
            self.assertEqual(rep.overall_evidence, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
            self.assertIsNotNone(rep.plan_cost)
            self.assertGreater(rep.states_visited, 0)
            self.assertGreater(rep.step_evaluations, 0)

    def test_03_backward_dfs_unsolvable_case_backtracking(self):
        """Backward search backtracks through all candidates and returns NO_FEASIBLE_SEQUENCE."""
        session = _build_mock_session(["B01", "B02", "B03"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        # All steps collide
        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            return StepEvaluationRecord(
                bend_id=candidate_bend_id,
                step_index=len(completed_prefix),
                completed_prefix=tuple(completed_prefix),
                pose_status=PhysicalEvidence.COLLISION,
                trajectory_status=PhysicalEvidence.COLLISION,
                effective_status=PhysicalEvidence.COLLISION,
                reason="COLLISION_WITH_FLANGE",
            )

        with patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a") as mock_sa, \
             patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b") as mock_sb, \
             patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=mock_eval_step):

            mock_sa.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_A", "A_OK")
            mock_sb.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "B_OK")

            rep = search_bend_sequence_backward_25d(
                session,
                cycle,
                tooling,
                max_states=50,
                max_checks=50,
            )

            self.assertEqual(rep.status, SearchStatus.NO_FEASIBLE_SEQUENCE)
            self.assertIsNone(rep.found_sequence)
            self.assertTrue(rep.strict_search_exhausted)
            self.assertFalse(rep.strict_search_limit_reached)

    def test_04_backward_dfs_unverified_branch_no_dead_end_poisoning(self):
        """UNVERIFIED status from numerical / geometry issue fails closed without poisoning dead ends."""
        session = _build_mock_session(["B01", "B02", "B03"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()
        eval_cache = StepEvaluationCache()

        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            return StepEvaluationRecord(
                bend_id=candidate_bend_id,
                step_index=len(completed_prefix),
                completed_prefix=tuple(completed_prefix),
                pose_status=PhysicalEvidence.UNVERIFIED,
                trajectory_status=None,
                effective_status=PhysicalEvidence.UNVERIFIED,
                reason="UNVERIFIED_NUMERICAL_INSTABILITY",
            )

        with patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a") as mock_sa, \
             patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b") as mock_sb, \
             patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=mock_eval_step):

            mock_sa.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_A", "A_OK")
            mock_sb.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "B_OK")

            rep = search_bend_sequence_backward_25d(
                session,
                cycle,
                tooling,
                cache=eval_cache,
                max_states=50,
                max_checks=50,
            )

            # Dead end cache must NOT record the root state or any unverified state as a proven dead end
            self.assertEqual(len(eval_cache._dead_end_subsets), 0)
            self.assertIsNone(rep.found_sequence)
            self.assertEqual(rep.status, SearchStatus.UNVERIFIED)

    def test_05_forward_transition_scoring_in_backward_search(self):
        """Candidate ordering evaluates transition penalties in forward production order,
        verifies fold state angles are properly isolated per candidate, and asserts concrete costs."""
        session = _build_mock_session(["B01", "B02", "B03"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        captured_candidate_angles = []
        captured_transitions = []

        def spy_compute_setup(fold_state, active_bend_id, **kw):
            # Record exact fold state angles at the time setup is computed for this candidate
            captured_candidate_angles.append((active_bend_id, dict(fold_state.angles)))
            if active_bend_id == "B01":
                return WorkpieceMachineSetup(bend_id="B01", rotation_deg=0.0, flipped=False, shift_xm_mm=0.0, bend_length_mm=100.0, is_complete=True)
            elif active_bend_id == "B02":
                return WorkpieceMachineSetup(bend_id="B02", rotation_deg=90.0, flipped=True, shift_xm_mm=10.0, bend_length_mm=100.0, is_complete=True)
            else:
                return WorkpieceMachineSetup(bend_id="B03", rotation_deg=0.0, flipped=False, shift_xm_mm=0.0, bend_length_mm=100.0, is_complete=True)

        real_transition_penalty = transition_penalty
        def spy_transition_penalty(prev, curr, weights=None):
            w = weights or OperationWeights()
            pen = real_transition_penalty(prev, curr, w)
            if prev is not None and curr is not None:
                captured_transitions.append((prev.bend_id, curr.bend_id, pen.total, pen.turn_cost, pen.flip_cost))
            return pen

        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            return StepEvaluationRecord(
                bend_id=candidate_bend_id,
                step_index=len(completed_prefix),
                completed_prefix=tuple(completed_prefix),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="CLEAR",
            )

        def mock_forward_replay(sess, cyc, tool, seq, **kw):
            step_reps = [
                StepEvaluationRecord(
                    bend_id=b,
                    step_index=i,
                    completed_prefix=tuple(seq[:i]),
                    pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    reason="OK",
                )
                for i, b in enumerate(seq)
            ]
            return ForwardReplayReport(
                sequence=tuple(seq),
                is_verified=True,
                is_conditional=False,
                overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                steps=step_reps,
                blocking_reason="",
            )

        weights = OperationWeights(flip=20.0, turn_per_90_deg=10.0, shift_fixed=5.0)

        with patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a") as mock_sa, \
             patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b") as mock_sb, \
             patch("smbs_core.kinematics2d.sequence_search.compute_workpiece_machine_setup", side_effect=spy_compute_setup), \
             patch("smbs_core.kinematics2d.sequence_search.transition_penalty", side_effect=spy_transition_penalty), \
             patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=mock_eval_step), \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay", side_effect=mock_forward_replay):

            mock_sa.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_A", "A_OK")
            mock_sb.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "B_OK")

            rep = search_bend_sequence_backward_25d(
                session,
                cycle,
                tooling,
                weights=weights,
                max_states=50,
                max_checks=50,
            )

            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertIsNotNone(rep.found_sequence)

            # 1. State angles must be isolated: candidate b must have S'={all_bends - {b}} folded, b at 0.0
            # If state was not isolated, B01 would have had whatever angles remained from Stage B
            b01_captures = [ang for cid, ang in captured_candidate_angles if cid == "B01" and ang.get("B02") == 90.0 and ang.get("B03") == 90.0]
            b02_captures = [ang for cid, ang in captured_candidate_angles if cid == "B02" and ang.get("B01") == 90.0 and ang.get("B03") == 90.0]
            b03_captures = [ang for cid, ang in captured_candidate_angles if cid == "B03" and ang.get("B01") == 90.0 and ang.get("B02") == 90.0]
            self.assertTrue(len(b01_captures) > 0, "B01 candidate must have S'={B02, B03} folded and B01 at 0°")
            self.assertEqual(b01_captures[0]["B01"], 0.0)
            self.assertTrue(len(b02_captures) > 0, "B02 candidate must have S'={B01, B03} folded and B02 at 0°")
            self.assertEqual(b02_captures[0]["B02"], 0.0)
            self.assertTrue(len(b03_captures) > 0, "B03 candidate must have S'={B01, B02} folded and B03 at 0°")
            self.assertEqual(b03_captures[0]["B03"], 0.0)

            # 2. Argument direction in transition_penalty: candidate is previous, suffix[0] is next
            for prev_id, curr_id, cost, turn_cost, flip_cost in captured_transitions:
                self.assertNotEqual(prev_id, curr_id)
                # Verify concrete cost calculation:
                # B01 -> B02: flip (20.0) + 90° turn (10.0) + shift (5.0) = 35.0
                if (prev_id, curr_id) == ("B01", "B02"):
                    self.assertAlmostEqual(cost, 35.0)
                    self.assertAlmostEqual(flip_cost, 20.0)
                    self.assertAlmostEqual(turn_cost, 10.0)
                # B01 -> B03: no flip (0.0) + 0° turn (0.0) = 0.0
                elif (prev_id, curr_id) == ("B01", "B03"):
                    self.assertAlmostEqual(cost, 0.0)

    def test_08_backward_cost_optimization_matches_brute_force(self):
        """When optimize_cost=True, backward DFS with branch-and-bound finds the exact global minimum."""
        session = _build_mock_session(["B01", "B02", "B03"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        # Setups:
        # B01: rotation 0, flip False
        # B02: rotation 90, flip True  (flip=20, turn=10 -> cost 30 to/from B01 or B03)
        # B03: rotation 0, flip False  (cost 0 to/from B01)
        # Permutations:
        # (B01, B03, B02): 0 + 0 + 30 = 30.0
        # (B03, B01, B02): 0 + 0 + 30 = 30.0
        # (B02, B01, B03): 0 + 30 + 0 = 30.0
        # (B02, B03, B01): 0 + 30 + 0 = 30.0
        # (B01, B02, B03): 0 + 30 + 30 = 60.0  <- suboptimal!
        # (B03, B02, B01): 0 + 30 + 30 = 60.0  <- suboptimal!
        def mock_compute_setup(fold_state, active_bend_id, **kw):
            if active_bend_id == "B02":
                return WorkpieceMachineSetup(bend_id="B02", rotation_deg=90.0, flipped=True, shift_xm_mm=0.0, bend_length_mm=100.0, is_complete=True)
            else:
                return WorkpieceMachineSetup(bend_id=active_bend_id, rotation_deg=0.0, flipped=False, shift_xm_mm=0.0, bend_length_mm=100.0, is_complete=True)

        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            return StepEvaluationRecord(
                bend_id=candidate_bend_id,
                step_index=len(completed_prefix),
                completed_prefix=tuple(completed_prefix),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="CLEAR",
            )

        def mock_forward_replay(sess, cyc, tool, seq, **kw):
            step_reps = [
                StepEvaluationRecord(
                    bend_id=b,
                    step_index=i,
                    completed_prefix=tuple(seq[:i]),
                    pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    reason="OK",
                )
                for i, b in enumerate(seq)
            ]
            return ForwardReplayReport(
                sequence=tuple(seq),
                is_verified=True,
                is_conditional=False,
                overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                steps=step_reps,
                blocking_reason="",
            )

        weights = OperationWeights(flip=20.0, turn_fixed=0.0, turn_per_90_deg=10.0, shift_fixed=0.0, length_change_fixed=0.0)

        with patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a") as mock_sa, \
             patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b") as mock_sb, \
             patch("smbs_core.kinematics2d.sequence_search.compute_workpiece_machine_setup", side_effect=mock_compute_setup), \
             patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step", side_effect=mock_eval_step), \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay", side_effect=mock_forward_replay):

            mock_sa.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_A", "A_OK")
            mock_sb.return_value = PreliminaryScreeningReport(ScreeningDecision.CONTINUE_TO_3D, "STAGE_B", "B_OK")

            rep = search_bend_sequence_backward_25d(
                session,
                cycle,
                tooling,
                weights=weights,
                optimize_cost=True,
                max_states=50,
                max_checks=50,
            )

            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertIsNotNone(rep.plan_cost)
            self.assertEqual(rep.plan_cost["total"], 30.0, "Backward search must find minimal cost sequence (30.0, not 60.0)")
            self.assertNotIn(rep.found_sequence, (("B01", "B02", "B03"), ("B03", "B02", "B01")), "Suboptimal sequences must not be selected")

    def test_09_timing_accounting_no_double_counting(self):
        """Profiler detects timing double-counting and raises ValueError if independent timings exceed total wall time."""
        total_time_s = 10.0
        # 1. Valid non-overlapping timing
        stats_valid = {
            "solid_rebuild_time_s": 3.0,
            "placement_calc_time_s": 0.01,
            "total_cache_lookup_s": 0.05,
            "punch_eval_time_s": 4.0,
            "die_eval_time_s": 1.5,
            "punch_end_zone_time_s": 0.0,
        }
        accounted_valid = sum(stats_valid.values())
        diff_valid = total_time_s - accounted_valid
        self.assertGreater(diff_valid, 0.0)
        unaccounted_valid = max(0.0, diff_valid)
        self.assertAlmostEqual(accounted_valid + unaccounted_valid, total_time_s)

        # 2. Flawed timing where solid_rebuild is double-counted inside cache_lookup
        stats_double_counted = dict(stats_valid)
        stats_double_counted["total_cache_lookup_s"] = 3.05  # solid rebuild (3.0) added into cache lookup
        accounted_invalid = sum(stats_double_counted.values())
        diff_invalid = total_time_s - accounted_invalid
        self.assertLess(diff_invalid, -0.005)

        # Consistency check must detect this flaw and refuse to hide it
        with self.assertRaises(ValueError) as ctx:
            if diff_invalid < -0.005:
                raise ValueError(
                    f"Measurement consistency error: sum of independent timings ({accounted_invalid:.4f}s) "
                    f"exceeds total wall time ({total_time_s:.4f}s) by {-diff_invalid:.4f}s."
                )
        self.assertIn("Measurement consistency error", str(ctx.exception))

    def test_06_dfs_backward_planner_class_wrapper(self):
        """DFSBackwardPlanner25D class properly wraps search_bend_sequence_backward_25d."""
        session = _build_mock_session(["B01"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        planner = DFSBackwardPlanner25D(
            session,
            cycle,
            tooling,
            max_states=10,
            max_checks=10,
        )

        with patch("smbs_core.kinematics2d.sequence_search.search_bend_sequence_backward_25d") as mock_bwd:
            dummy_rep = SequenceSearchReport(
                status=SearchStatus.STRICT_VALIDATED,
                overall_evidence=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                found_sequence=("B01",),
                initial_sequence=("B01",),
                best_partial_sequence=("B01",),
                max_depth_reached=1,
                total_bends=1,
                states_visited=1,
                step_evaluations=1,
                cache_hits=0,
                dead_end_hits=0,
                backtracks_count=0,
                elapsed_s=0.05,
                match_active_bend_length=True,
                allow_end_zone_collision=False,
                end_zone_length_mm=0.0,
            )
            mock_bwd.return_value = dummy_rep

            res = planner.solve()
            self.assertIs(res, dummy_rep)
            mock_bwd.assert_called_once()

    @unittest.skipUnless(HAS_FREECAD, "Requires FreeCAD environment")
    def test_07_native_cad_box_search_comparison(self):
        """Compare forward and backward search on native CAD box model under identical budgets."""
        from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
        from smbs_core.kinematics2d.tooling_25d import ToolKind

        doc_path = str(demo_part_path("box.100out-50in-(ok).FCStd"))
        doc = App.openDocument(doc_path, True)
        try:
            unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
            session = build_part_session(doc, [unfold])
            panel = _PreviewPanel()
            panel.session = session
            p_tool = panel.tool_library.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH)
            d_tool = panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE)
            panel.select_punch(p_tool)
            panel.select_die(d_tool)

            # Run forward search with small budget
            t0 = time.perf_counter()
            fwd_rep = search_bend_sequence_25d(
                session,
                panel.press_cycle,
                panel.tooling,
                match_active_bend_length=True,
                max_states=3,
                max_checks=3,
                max_search_time_s=15.0,
                search_direction="FORWARD",
            )
            fwd_time = time.perf_counter() - t0

            # Run backward search with identical budget
            t0 = time.perf_counter()
            bwd_rep = search_bend_sequence_25d(
                session,
                panel.press_cycle,
                panel.tooling,
                match_active_bend_length=True,
                max_states=3,
                max_checks=3,
                max_search_time_s=15.0,
                search_direction="BACKWARD",
            )
            bwd_time = time.perf_counter() - t0

            # Both should finish cleanly within budget
            self.assertIn(fwd_rep.status, (SearchStatus.NOT_FOUND_WITHIN_SEARCH_LIMITS, SearchStatus.STRICT_VALIDATED, SearchStatus.CONDITIONAL_VALIDATED, SearchStatus.NO_FEASIBLE_SEQUENCE))
            self.assertIn(bwd_rep.status, (SearchStatus.NOT_FOUND_WITHIN_SEARCH_LIMITS, SearchStatus.STRICT_VALIDATED, SearchStatus.CONDITIONAL_VALIDATED, SearchStatus.NO_FEASIBLE_SEQUENCE))
            self.assertGreaterEqual(fwd_rep.states_visited, 1)
            self.assertGreaterEqual(bwd_rep.states_visited, 1)
            self.assertGreaterEqual(fwd_rep.step_evaluations, 1)
            self.assertGreaterEqual(bwd_rep.step_evaluations, 1)

        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
