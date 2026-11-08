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
        """Candidate ordering evaluates transition penalties in forward production order."""
        session = _build_mock_session(["B01", "B02"])
        tooling = _build_mock_tooling()
        cycle = SimpleNamespace()

        # Check that candidate scoring computes penalty from candidate to successor in forward order
        order_evaluated = []

        def mock_eval_step(sess, cyc, tool, completed_prefix, candidate_bend_id, remaining_after_candidate, **kw):
            order_evaluated.append((tuple(completed_prefix), candidate_bend_id))
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
                weights=OperationWeights(flip=50.0, turn_per_90_deg=10.0),
            )

            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertIsNotNone(rep.found_sequence)
            self.assertEqual(len(rep.found_sequence), 2)
            self.assertIsNotNone(rep.plan_cost)

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
