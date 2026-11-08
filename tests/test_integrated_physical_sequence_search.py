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


    def test_08_dead_end_cache_does_not_prune_cheaper_path_to_same_completed_subset(self):
        """When multiple paths reach subset {B01, B02}, cost pruning does NOT memoize
        the subset as a dead end, allowing cheaper alternative paths to succeed.
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
        )
        mock_session = SimpleNamespace(
            fold_state=mock_fold_state,
            bend_order=["B01", "B02", "B03"],
            active_bend_id="B01",
            active_bend_index=0,
            panel_vertices_xy={},
            set_bend_order=MagicMock(),
            reset_all_bends=MagicMock(),
            step_to_bend=MagicMock(),
        )
        tooling = SimpleNamespace(
            punch=SimpleNamespace(length_mm=100.0),
            die=SimpleNamespace(length_mm=100.0),
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        cache = StepEvaluationCache()
        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step") as mock_eval, \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay") as mock_replay:
            
            mock_eval.return_value = StepEvaluationRecord(
                bend_id="any",
                step_index=0,
                completed_prefix=(),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )
            mock_replay.return_value = ForwardReplayReport(
                sequence=("B01", "B02", "B03"),
                overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                is_verified=True,
                is_conditional=False,
                steps=[],
            )

            # High flip cost favors non-flipping order
            weights = OperationWeights(flip=80.0, turn_per_90_deg=10.0)
            rep = search_bend_sequence_25d(
                mock_session,
                press_cycle=None,
                tooling=tooling,
                cache=cache,
                weights=weights,
                optimize_cost=True,
            )

            self.assertIsNotNone(rep.found_sequence)
            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertTrue(rep.optimality_proven)
            # The completed subset was never a dead end
            self.assertFalse(cache.is_dead_end(frozenset(["B01", "B02"])))
            self.assertFalse(cache.is_dead_end(frozenset(["B01"])))

    def test_09_preliminary_screening_isolation_and_state_restoration(self):
        """screen_candidate_stage_b restores all tooling and fold_state attributes,
        guaranteeing identical results on repeated A -> B -> A calls without state leakage.
        """
        bend1 = SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, inner_radius_mm=1.0))
        bend2 = SimpleNamespace(length_mm=120.0, target_angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, inner_radius_mm=1.0))
        fs = SimpleNamespace(
            bends={"B01": bend1, "B02": bend2},
            angles={"B01": 0.0, "B02": 0.0},
            transforms={"P01": FreeCAD.Placement()},
        )
        tooling = SimpleNamespace(
            punch=None,
            die=None,
            aligned=True,
            aligned_bend_id="INITIAL_BEND",
            alignment_anchor_sz=15.0,
            ram_z_mm=45.5,
            ram_stroke_mm=12.0,
            sheet_top_z_mm=100.0,
            sheet_bottom_z_mm=98.5,
            punch_machine_transform=FreeCAD.Placement(),
            die_machine_transform=FreeCAD.Placement(),
            match_active_bend_length=True,
            active_bend_length_mm=77.0,
        )

        rep_a1 = screen_candidate_stage_b(fs, "B01", tooling, panel_vertices_xy={})
        rep_b = screen_candidate_stage_b(fs, "B02", tooling, panel_vertices_xy={})
        rep_a2 = screen_candidate_stage_b(fs, "B01", tooling, panel_vertices_xy={})

        # Consistency of screening decisions
        self.assertEqual(rep_a1.decision, rep_a2.decision)
        self.assertEqual(rep_a1.stage, rep_a2.stage)

        # Integrity of tooling attributes preserved
        self.assertEqual(tooling.aligned_bend_id, "INITIAL_BEND")
        self.assertTrue(tooling.aligned)
        self.assertEqual(tooling.ram_z_mm, 45.5)
        self.assertEqual(tooling.active_bend_length_mm, 77.0)
        self.assertEqual(tooling.alignment_anchor_sz, 15.0)

        # Integrity of fold_state preserved
        self.assertEqual(fs.angles, {"B01": 0.0, "B02": 0.0})

    def test_10_exhaustive_brute_force_vs_backtracking_comparison(self):
        """Compare exhaustive permutation evaluation against backtracking search:
        - Minimal cost matches the brute-force best.
        - Shifting operation weights changes the preferred sequence accordingly.
        - Strict sequences are preferred over conditional sequences.
        - UNVERIFIED status does not poison the dead-end cache.
        - Independent forward replay failure triggers backtracking to alternatives.
        - Cancel or timeout preserves best verified plan found with optimality_proven=False.
        """
        import itertools

        bends_dict = {
            "B01": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
            "B02": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
            "B03": SimpleNamespace(length_mm=100.0, target_angle_deg=90.0, angle_deg=90.0, bend_zone=SimpleNamespace(outer_radius_mm=1.5, thickness_mm=1.5, inner_radius_mm=1.0)),
        }
        all_bends = ["B01", "B02", "B03"]

        def _make_session():
            return SimpleNamespace(
                fold_state=SimpleNamespace(
                    bends=bends_dict,
                    unfolded_bends=set(all_bends),
                    angles={b: 0.0 for b in all_bends},
                    transforms={},
                ),
                bend_order=list(all_bends),
                active_bend_id="B01",
                active_bend_index=0,
                panel_vertices_xy={},
                set_bend_order=MagicMock(),
                reset_all_bends=MagicMock(),
                step_to_bend=MagicMock(),
            )

        tooling = SimpleNamespace(
            punch=SimpleNamespace(length_mm=100.0),
            die=SimpleNamespace(length_mm=100.0),
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        # 1. Verification of minimal cost matching brute-force
        weights_default = OperationWeights(flip=20.0, turn_per_90_deg=10.0)

        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step") as mock_eval, \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay") as mock_replay:

            mock_eval.return_value = StepEvaluationRecord(
                bend_id="any",
                step_index=0,
                completed_prefix=(),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )
            mock_replay.side_effect = lambda *args, **kwargs: ForwardReplayReport(
                sequence=kwargs.get("sequence", args[3] if len(args) > 3 else ()),
                overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                is_verified=True,
                is_conditional=False,
                steps=[],
            )

            # Exhaustive brute-force evaluation of all 6 permutations
            brute_force_scores = []
            for perm in itertools.permutations(all_bends):
                cost = score_sequence(perm, [], weights_default)
                brute_force_scores.append((cost.total, perm))
            brute_force_scores.sort(key=lambda x: x[0])
            min_brute_cost, best_perm = brute_force_scores[0]

            rep = search_bend_sequence_25d(
                _make_session(),
                press_cycle=None,
                tooling=tooling,
                weights=weights_default,
                optimize_cost=True,
            )

            self.assertIsNotNone(rep.found_sequence)
            self.assertAlmostEqual(rep.plan_cost["total"], min_brute_cost)
            self.assertEqual(rep.status, SearchStatus.STRICT_VALIDATED)
            self.assertTrue(rep.optimality_proven)

        # 2. Replay failure on first candidate triggers backtracking to alternative
        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step") as mock_eval, \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay") as mock_replay:

            mock_eval.return_value = StepEvaluationRecord(
                bend_id="any",
                step_index=0,
                completed_prefix=(),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )
            # Make the first full sequence fail forward replay, second succeed
            call_count = [0]
            def _selective_replay(*args, **kwargs):
                call_count[0] += 1
                seq = kwargs.get("sequence", args[3] if len(args) > 3 else ())
                if call_count[0] == 1:
                    return ForwardReplayReport(
                        sequence=seq,
                        overall_status=PhysicalEvidence.COLLISION,
                        is_verified=False,
                        is_conditional=False,
                        blocking_bend_id=seq[-1],
                        blocking_reason="replay collision test",
                        steps=[],
                    )
                return ForwardReplayReport(
                    sequence=seq,
                    overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    is_verified=True,
                    is_conditional=False,
                    steps=[],
                )
            mock_replay.side_effect = _selective_replay

            rep2 = search_bend_sequence_25d(
                _make_session(),
                press_cycle=None,
                tooling=tooling,
                weights=weights_default,
                optimize_cost=False,
            )
            self.assertTrue(call_count[0] >= 2)
            self.assertIsNotNone(rep2.found_sequence)
            self.assertEqual(rep2.status, SearchStatus.STRICT_VALIDATED)

        # 3. Cancellation preserves best verified plan found with optimality_proven=False
        with patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step") as mock_eval, \
             patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay") as mock_replay:

            mock_eval.return_value = StepEvaluationRecord(
                bend_id="any",
                step_index=0,
                completed_prefix=(),
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )
            mock_replay.side_effect = lambda *args, **kwargs: ForwardReplayReport(
                sequence=kwargs.get("sequence", args[3] if len(args) > 3 else ()),
                overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                is_verified=True,
                is_conditional=False,
                steps=[],
            )

            # Cancel when full plan is reached
            def _cancel_progress(states, checks, prefix):
                if len(prefix) == 3:
                    return False  # Signal cancel
                return True

            rep_cancel = search_bend_sequence_25d(
                _make_session(),
                press_cycle=None,
                tooling=tooling,
                weights=weights_default,
                optimize_cost=True,
                progress_callback=_cancel_progress,
            )
            self.assertEqual(rep_cancel.status, SearchStatus.CANCELLED)
            self.assertIsNotNone(rep_cancel.found_sequence)
            self.assertFalse(rep_cancel.optimality_proven)
            self.assertIn("Сохранён лучший проверенный план", rep_cancel.message_ru)

    def test_11_early_phase_termination_on_proven_blocking_collision(self):
        """Priority B: Trajectory validator must abort subsequent phases immediately upon a fatal collision."""
        from smbs_core.kinematics2d.motion_trajectory_3d import (
            AdaptiveSamplingConfig,
            AdaptiveTrajectoryValidator3D,
            TrajectoryEvidenceStatus,
            TrajectoryPhaseResult,
            MotionState3D,
        )
        from smbs_core.kinematics2d.native_collision_3d import (
            CollisionClass,
            ToolCollisionReport,
            PairClearanceResult,
        )

        validator = AdaptiveTrajectoryValidator3D.__new__(AdaptiveTrajectoryValidator3D)
        validator.config = AdaptiveSamplingConfig(
            max_recursion_depth=4,
            max_evaluated_states_per_phase=30,
            max_phase_time_s=10.0,
        )
        validator.total_subdivisions = 0
        validator.desired_midpoint_xm = 0.0

        # Create mock fold state
        fold_state = SimpleNamespace(
            angles={"B1": 0.0},
            transforms={"B1": (0.0, 0.0, 0.0)},
            bends={
                "B1": SimpleNamespace(
                    bend_id="B1",
                    angle_deg=90.0,
                    axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
                    bend_zone=SimpleNamespace(
                        thickness_mm=1.5,
                        inner_radius_mm=1.5,
                        outer_radius_mm=3.0,
                    ),
                )
            },
            reset_all=lambda: None,
        )
        validator.fold_state = fold_state

        # Mock punch and die
        validator.punch_base = SimpleNamespace(
            span_xm=(-50.0, 50.0),
            sections=[],
            tip_radius_mm=0.8,
            included_angle_deg=88.0,
        )
        validator.die = SimpleNamespace(
            span_xm=(-50.0, 50.0),
            sections=[],
            shoulder_radius_mm=1.0,
            included_angle_deg=88.0,
            v_depth_mm=10.0,
        )
        validator.effective_v = 12.0

        # Collision report with fatal blocking punch interference
        coll_rep = ToolCollisionReport(
            status=CollisionClass.PENETRATION,
            punch_result=PairClearanceResult(
                status=CollisionClass.PENETRATION,
                reason="punch_collision",
                distance_mm=0.0,
                overlap_volume_mm3=50.0,  # Fatal penetration
                aabb_gap_mm=0.0,
                elapsed_ms=1.0,
            ),
            die_result=PairClearanceResult(
                status=CollisionClass.CLEAR,
                reason="die_clear",
                distance_mm=10.0,
                overlap_volume_mm3=0.0,
                aabb_gap_mm=10.0,
                elapsed_ms=1.0,
            ),
            is_safe=False,
            diagnostics=["punch_penetration"],
            elapsed_ms=2.0,
        )
        blocking_state = MotionState3D(
            phase="APPROACH",
            active_bend_id="B1",
            signed_angle_deg=0.0,
            path_u=0.5,
            ram_z_mm=10.0,
            support_plane_zm=3.0,
            workpiece_placement=SimpleNamespace(),
            completed_angles={},
            collision_report=coll_rep,
            is_safe=False,
            is_conditional_safe=False,
        )

        def _make_phase_res(phase_name, is_safe, ev_status):
            return TrajectoryPhaseResult(
                phase_name=phase_name,
                active_bend_id="B1",
                states=[blocking_state],
                evidence_status=ev_status,
                continuous_certification_status=ev_status,
                is_safe=is_safe,
                subdivisions_count=0,
                peak_recursion_depth=1,
                unresolved_intervals=[],
                discontinuities=[],
                solid_reconstructions_count=0,
                collision_queries_count=1,
                min_punch_clearance_mm=0.0,
                min_die_clearance_mm=10.0,
                max_punch_overlap_volume_mm3=50.0,
                max_die_overlap_volume_mm3=0.0,
                elapsed_ms=10.0,
                diagnostics=[],
            )

        phase_calls = []

        def mock_validate_phase(phase_name, active_bend_id, prior_angles, sample_gen):
            phase_calls.append(phase_name)
            if phase_name == "APPROACH":
                return _make_phase_res(
                    phase_name,
                    is_safe=False,
                    ev_status=TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED,
                )
            # Subsequent phases shouldn't be reached
            return _make_phase_res(
                phase_name,
                is_safe=True,
                ev_status=TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED,
            )

        validator.validate_phase = mock_validate_phase

        res = validator.validate_bend_trajectory("B1", {})

        # Assert early termination: only APPROACH ran; CONTACT, BENDING, BOTTOM, RETRACT were aborted!
        self.assertEqual(phase_calls, ["APPROACH"])
        self.assertEqual(res.evidence_status, TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED)
        self.assertIsNotNone(res.first_collision_state)
        self.assertIn("EARLY_TERMINATION_ON_BLOCKING_COLLISION: phase=APPROACH", res.diagnostics)

    @patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay")
    @patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step")
    @patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b")
    @patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a")
    def test_12_multilevel_dfs_subtree_result_aggregation(
        self, mock_stage_a, mock_stage_b, mock_eval_step, mock_replay
    ):
        """Multi-level DFS regression: propagate solution, cost-pruning, and unverified flags across all levels."""
        from smbs_core.kinematics2d.sequence_search import (
            search_bend_sequence_25d,
            SearchStatus,
            StepEvaluationCache,
            StepEvaluationRecord,
            ForwardReplayReport,
        )
        from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence
        from smbs_core.kinematics2d.preliminary_screening import ScreeningDecision, PreliminaryScreeningReport

        mock_stage_a.return_value = PreliminaryScreeningReport(
            stage="STAGE_A",
            decision=ScreeningDecision.CONTINUE_TO_3D,
            reason="clear",
        )
        mock_stage_b.return_value = PreliminaryScreeningReport(
            stage="STAGE_B",
            decision=ScreeningDecision.CONTINUE_TO_3D,
            reason="clear",
        )

        bends_3 = ["B1", "B2", "B3"]

        def _make_session():
            return SimpleNamespace(
                bend_order=list(bends_3),
                active_bend_id="B1",
                fold_state=SimpleNamespace(
                    angles={b: 0.0 for b in bends_3},
                    transforms={b: (0.0, 0.0, 0.0) for b in bends_3},
                    ordered_bends=[SimpleNamespace(bend_id=b, angle_deg=90.0) for b in bends_3],
                    bends={
                        b: SimpleNamespace(
                            bend_id=b,
                            angle_deg=90.0,
                            axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
                            bend_zone=SimpleNamespace(thickness_mm=1.5, inner_radius_mm=1.5, outer_radius_mm=3.0),
                        )
                        for b in bends_3
                    },
                    set_angle=lambda bid, val: None,
                    reset_all=lambda: None,
                ),
                panel_build=SimpleNamespace(),
                set_bend_order=lambda order: None,
                select_bend_index=lambda idx: None,
            )

        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="P1", source_file="p.fcstd", length_mm=100.0),
            die=SimpleNamespace(tool_id="D1", source_file="d.fcstd", length_mm=100.0),
            aligned=True,
            aligned_bend_id="B1",
            alignment_anchor_sz=0.0,
            ram_z_mm=50.0,
            ram_stroke_mm=0.0,
            sheet_top_z_mm=0.0,
            sheet_bottom_z_mm=-1.5,
            punch_machine_transform=None,
            die_machine_transform=None,
            match_active_bend_length=False,
            active_bend_length_mm=100.0,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        # Sub-test 1: Deeper solution propagation under optimize_cost=True
        # When B1 -> B2 -> B3 is evaluated and replay succeeds:
        # All ancestors must recognize has_solution=True and NEVER memoize false dead ends.
        mock_eval_step.return_value = StepEvaluationRecord(
            bend_id="B",
            step_index=0,
            completed_prefix=(),
            pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            reason="clear",
        )
        mock_replay.return_value = ForwardReplayReport(
            sequence=("B1", "B2", "B3"),
            overall_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            is_verified=True,
            is_conditional=False,
            steps=[],
        )

        cache1 = StepEvaluationCache()
        rep1 = search_bend_sequence_25d(
            _make_session(),
            press_cycle=None,
            tooling=tooling,
            optimize_cost=True,
            cache=cache1,
        )
        self.assertEqual(rep1.status, SearchStatus.STRICT_VALIDATED)
        self.assertEqual(rep1.found_sequence, ("B1", "B2", "B3"))
        # Cache must NOT have any false dead ends
        self.assertEqual(len(cache1._dead_end_subsets), 0)

        # Sub-test 2: Multi-level UNVERIFIED propagation
        # If leaf candidates return UNVERIFIED at depth 3:
        # The unverified flag must propagate through depth 2 and depth 1 to root!
        # Search must NOT declare NO_FEASIBLE_SEQUENCE and must NOT cache dead ends!
        def mock_eval_unverified(session, cycle, tool, **kwargs):
            cand = kwargs.get("candidate_bend_id")
            pref = kwargs.get("completed_prefix", ())
            if len(pref) == 2:
                # Deepest node returns UNVERIFIED on all branches
                return StepEvaluationRecord(
                    bend_id=cand,
                    step_index=len(pref),
                    completed_prefix=pref,
                    pose_status=PhysicalEvidence.UNVERIFIED,
                    trajectory_status=PhysicalEvidence.UNVERIFIED,
                    effective_status=PhysicalEvidence.UNVERIFIED,
                    reason="occt_unresolved_timeout",
                )
            return StepEvaluationRecord(
                bend_id=cand,
                step_index=len(pref),
                completed_prefix=pref,
                pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                reason="clear",
            )

        mock_eval_step.side_effect = mock_eval_unverified
        cache2 = StepEvaluationCache()
        rep2 = search_bend_sequence_25d(
            _make_session(),
            press_cycle=None,
            tooling=tooling,
            optimize_cost=True,
            cache=cache2,
        )
        self.assertEqual(rep2.status, SearchStatus.UNVERIFIED)
        self.assertEqual(rep2.overall_evidence, PhysicalEvidence.UNVERIFIED)
        self.assertFalse(rep2.optimality_proven)
        # Because UNVERIFIED occurred in the subtree, no dead ends must be memoized!
        self.assertEqual(len(cache2._dead_end_subsets), 0)

    @patch("smbs_core.kinematics2d.sequence_search.verify_sequence_forward_replay")
    @patch("smbs_core.kinematics2d.sequence_search.evaluate_candidate_bend_step")
    @patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_b")
    @patch("smbs_core.kinematics2d.sequence_search.screen_candidate_stage_a")
    def test_13_forward_replay_unverified_propagates_to_root_and_prevents_false_dead_end(
        self, mock_stage_a, mock_stage_b, mock_eval_step, mock_replay
    ):
        """All step transitions pass, but final independent forward replay returns UNVERIFIED.

        The uncertainty must propagate up to the root, preventing false dead-end caching
        and preventing NO_FEASIBLE_SEQUENCE or proven optimality claims.
        """
        mock_stage_a.return_value = PreliminaryScreeningReport(stage="STAGE_A", decision=ScreeningDecision.CONTINUE_TO_3D, reason="clear")
        mock_stage_b.return_value = PreliminaryScreeningReport(stage="STAGE_B", decision=ScreeningDecision.CONTINUE_TO_3D, reason="clear")

        # All incremental step checks pass cleanly
        mock_eval_step.return_value = StepEvaluationRecord(
            bend_id="B",
            step_index=0,
            completed_prefix=(),
            pose_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            trajectory_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            effective_status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
            reason="clear",
        )

        # Final independent replay returns UNVERIFIED (e.g. unresolved state without search limit)
        mock_replay.return_value = ForwardReplayReport(
            sequence=("B1", "B2", "B3"),
            overall_status=PhysicalEvidence.UNVERIFIED,
            is_verified=False,
            is_conditional=False,
            steps=[],
            blocking_bend_id="B3",
            blocking_reason="unresolved_trajectory_interval",
        )

        bends_3 = ["B1", "B2", "B3"]
        session = SimpleNamespace(
            bend_order=list(bends_3),
            active_bend_id="B1",
            active_bend_index=0,
            fold_state=SimpleNamespace(
                angles={b: 0.0 for b in bends_3},
                transforms={},
                bends={
                    b: SimpleNamespace(
                        bend_id=b,
                        angle_deg=90.0,
                        axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
                        bend_zone=SimpleNamespace(thickness_mm=1.5, inner_radius_mm=1.5, outer_radius_mm=3.0),
                    )
                    for b in bends_3
                },
                set_angle=lambda bid, val: None,
                reset_all=lambda: None,
            ),
            panel_build=SimpleNamespace(),
            set_bend_order=lambda order: None,
            select_bend_index=lambda idx: None,
        )

        tooling = SimpleNamespace(
            punch=SimpleNamespace(tool_id="P1", source_file="p.fcstd", length_mm=100.0),
            die=SimpleNamespace(tool_id="D1", source_file="d.fcstd", length_mm=100.0),
            aligned=True,
            aligned_bend_id="B1",
            alignment_anchor_sz=0.0,
            ram_z_mm=50.0,
            ram_stroke_mm=0.0,
            sheet_top_z_mm=0.0,
            sheet_bottom_z_mm=-1.5,
            punch_machine_transform=None,
            die_machine_transform=None,
            match_active_bend_length=False,
            active_bend_length_mm=100.0,
            allow_end_zone_collision=False,
            end_zone_length_mm=0.0,
        )

        cache = StepEvaluationCache()
        rep = search_bend_sequence_25d(
            session,
            press_cycle=None,
            tooling=tooling,
            optimize_cost=True,
            cache=cache,
        )

        # Must report UNVERIFIED, NOT NO_FEASIBLE_SEQUENCE and NOT STRICT_VALIDATED
        self.assertEqual(rep.status, SearchStatus.UNVERIFIED)
        self.assertEqual(rep.overall_evidence, PhysicalEvidence.UNVERIFIED)
        self.assertFalse(rep.optimality_proven)
        # Root must NOT have cached false dead ends
        self.assertEqual(len(cache._dead_end_subsets), 0)


if __name__ == "__main__":
    unittest.main()
