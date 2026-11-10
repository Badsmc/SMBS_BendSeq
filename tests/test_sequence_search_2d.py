"""Planar DFS tests: no native 3D calls and no false physical certification."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.contact_oracle_2d import ContactStatus
from smbs_core.kinematics2d.preliminary_screening import (
    PreliminaryScreeningReport, ScreeningDecision,
)
from smbs_core.kinematics2d.sequence_search_2d import (
    PlanarSearchStatus, search_bend_sequences_2d,
)


class DummyState:
    def __init__(self, names):
        self.bends = {name: SimpleNamespace(
            angle_deg=90.0,
            axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
        ) for name in names}
        self.angles = {name: 17.0 for name in names}
        self.transforms = {"original": object()}

    def reset_all(self):
        self.angles = {name: 0.0 for name in self.bends}
        self.transforms = {"flat": 1}

    def set_angle(self, name, angle):
        self.angles[name] = angle
        self.transforms = {"updated": tuple(sorted(self.angles.items()))}


def stage_b_result(status="CLEAR", *, reason="sampled_2d_clear"):
    return PreliminaryScreeningReport(
        decision=ScreeningDecision.CONTINUE_TO_3D,
        stage="STAGE_B_PREVIEW",
        reason=reason,
        sampled_phases=("APPROACH", "BENDING", "BENDING", "BOTTOM", "RETRACT"),
        contact_statuses=(status,) * 5,
    )


class PlanarSearchTests(unittest.TestCase):
    def setUp(self):
        names = ("B01", "B02")
        self.state = DummyState(names)
        self.session = SimpleNamespace(
            fold_state=self.state, bend_order=names,
            panel_vertices_xy={"P": [(0.0, 0.0), (1.0, 0.0)]},
        )
        self.tooling = object()

    def run_mocked(self, side_effect=None, **opts):
        screen = side_effect if side_effect is not None else stage_b_result()
        with patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=screen if callable(screen) else None,
            return_value=None if callable(screen) else screen,
        ) as stage_b:
            result = search_bend_sequences_2d(
                self.session, self.tooling, fractions=(0.0, 0.5, 1.0), **opts
            )
        return result, stage_b

    def test_finds_planar_candidates_without_three_d(self):
        originals = (dict(self.state.angles), dict(self.state.transforms))
        result, stage_b = self.run_mocked()
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual([c.sequence for c in result.candidates],
                         [("B01", "B02"), ("B02", "B01")])
        self.assertEqual(stage_b.call_count, 4)
        self.assertEqual(self.state.angles, originals[0])
        self.assertEqual(self.state.transforms, originals[1])
        self.assertIn("NOT_3D", result.to_dict()["evidence"])

    def test_rejects_planar_interference_but_not_3d_certified(self):
        def effect(_state, bend, *_args, **_kwargs):
            if bend == "B01":
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "2d_interference",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()
        result, _ = self.run_mocked(effect)
        self.assertEqual(result.status, PlanarSearchStatus.EXHAUSTED)
        self.assertEqual(result.candidates, ())
        self.assertGreater(result.rejected_2d_interferences, 0)

    def test_ambiguity_is_not_clear(self):
        result, _ = self.run_mocked(stage_b_result("UNVERIFIED"))
        self.assertEqual(result.status, PlanarSearchStatus.UNVERIFIED_CANDIDATES)
        self.assertTrue(all(c.evidence == "UNVERIFIED_2D" for c in result.candidates))

    def test_exception_is_unverified_and_state_restored(self):
        def broken(*args, **kwargs):
            raise RuntimeError("section not available")
        before = dict(self.state.angles)
        result, _ = self.run_mocked(broken)
        self.assertEqual(result.status, PlanarSearchStatus.UNVERIFIED_CANDIDATES)
        self.assertEqual(self.state.angles, before)
        self.assertGreater(result.unverified_steps, 0)

    def test_limits_and_json(self):
        result, _ = self.run_mocked(max_solutions=1)
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual(result.complete_candidates_examined, 2)
        self.assertEqual(len(result.to_dict()["candidates"]), 1)

    def test_search_budget_limits_completed_candidates(self):
        result, _ = self.run_mocked(max_complete_candidates=1)
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual(result.limit_reason, "MAX_COMPLETE_CANDIDATES_REACHED")
        self.assertEqual(result.complete_candidates_examined, 1)

    def test_numbered_bends_and_parallel_axis_group(self):
        result, _ = self.run_mocked()
        self.assertEqual(len(result.axis_groups), 1)
        self.assertEqual(result.axis_groups[0][1], ("B01", "B02"))
        seq = result.candidates[0].to_dict()
        self.assertEqual([entry["number"] for entry in seq["numbered_bends"]], [1, 2])
        self.assertEqual([entry["axis_group"] for entry in seq["numbered_bends"]], ["AXIS_01", "AXIS_01"])

    def test_cost_ranking_across_axis_groups(self):
        from smbs_core.kinematics2d.operation_cost import WorkpieceMachineSetup
        names = ("B01", "B02", "B03")
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.state.bends["B03"].axis_xyz = ((0, 0, 0), (0, 100, 0))
        self.session.bend_order = names
        def setup(_state, bid, **kwargs):
            return WorkpieceMachineSetup(
                bid, 0.0 if bid != "B03" else 90.0, False,
                float(kwargs.get("desired_midpoint_xm", 0.0)), 100.0,
            )
        with patch("smbs_core.kinematics2d.sequence_search_2d.compute_workpiece_machine_setup", side_effect=setup):
            result, _ = self.run_mocked(max_solutions=6, max_complete_candidates=10)
        self.assertEqual(len(result.candidates), 6)
        self.assertEqual(len(result.axis_groups), 2)
        self.assertTrue(result.candidates[0].cost_complete)
        self.assertEqual(result.candidates[0].group_transitions, 1)
        self.assertTrue(any(c.group_transitions == 2 for c in result.candidates))
        self.assertTrue(all(result.candidates[i].cost <= result.candidates[i+1].cost for i in range(5)))

    def test_invalid_inputs(self):
        self.assertEqual(
            search_bend_sequences_2d(self.session, self.tooling, fractions=(0.5,)).status,
            PlanarSearchStatus.INVALID_INPUT,
        )
        self.assertEqual(
            search_bend_sequences_2d(self.session, self.tooling, max_solutions=0).status,
            PlanarSearchStatus.INVALID_INPUT,
        )

    def test_unchanged_bend_poses_per_prefix(self):
        seen = []
        def effect(state, bend, *_args, **_kwargs):
            seen.append((bend, tuple(sorted(state.angles.items()))))
            return stage_b_result()
        result, _ = self.run_mocked(effect)
        self.assertEqual(len(result.candidates), 2)
        self.assertIn(("B02", (("B01", 90.0), ("B02", 0.0))), seen)
        self.assertIn(("B01", (("B01", 0.0), ("B02", 90.0))), seen)

    def test_diagnostics_explain_timeout_and_deepest_prefix(self):
        result, _ = self.run_mocked(max_complete_candidates=1)
        payload = result.to_dict()
        self.assertEqual(payload["max_depth_reached"], 2)
        self.assertEqual(len(payload["deepest_prefix"]), 2)
        self.assertIn("rejection_counts", payload)
        self.assertIn("slowest_steps", payload)
        self.assertEqual(payload["complete_candidates_examined"], 1)
        self.assertEqual(payload["limit_reason"], "MAX_COMPLETE_CANDIDATES_REACHED")

    def test_diagnostics_explain_branch_rejections(self):
        def blocked(_state, bend, *_args, **_kwargs):
            if bend == "B01":
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "interference",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()
        result, _ = self.run_mocked(blocked)
        data = result.to_dict()
        self.assertEqual(data["max_depth_reached"], 1)
        self.assertGreater(data["rejection_counts"].get("2D_INTERFERENCE", 0), 0)

    def test_progressive_mode_replays_at_full_sampling_before_accepting(self):
        calls = []
        def screen(state, bid, *_args, **kwargs):
            fractions = tuple(kwargs["fractions"])
            calls.append((bid, fractions))
            # A collision absent from exploratory endpoints is detected by
            # the original full-density pass for B01 at the initial state.
            if bid == "B01" and len(fractions) == 3 and all(
                abs(angle) < 1e-9 for angle in state.angles.values()
            ):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "full_resolution_interference",
                    sampled_phases=("BENDING",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()
        result, _ = self.run_mocked(
            screen,
            exploration_fractions=(0.0, 1.0),
            max_complete_candidates=1,
        )
        self.assertEqual(result.candidates[0].sequence, ("B02", "B01"))
        self.assertGreaterEqual(result.final_replay_attempts, 2)
        self.assertEqual(result.final_replay_rejections, 1)
        self.assertTrue(any(len(fractions) == 2 for _, fractions in calls))
        self.assertTrue(any(len(fractions) == 3 for _, fractions in calls))

    def test_progressive_mode_keeps_full_sampled_evidence(self):
        result, stage_b = self.run_mocked(
            exploration_fractions=(0.0, 1.0),
            max_complete_candidates=1,
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.final_replay_attempts, 1)
        self.assertEqual(result.final_replay_rejections, 0)
        self.assertEqual(result.candidates[0].evidence, "SAMPLED_2D_CLEAR_OR_TOUCH")
        self.assertEqual(stage_b.call_count, 4)  # 2 coarse + 2 original-density

    def test_preferred_order_guides_exploration_without_skipping_checks(self):
        result, stage_b = self.run_mocked(
            preferred_bend_order=("B02", "B01"),
            group_first=False,
            max_complete_candidates=1,
        )
        self.assertEqual(result.candidates[0].sequence, ("B02", "B01"))
        self.assertEqual(result.complete_candidates_examined, 1)
        self.assertEqual(result.rejected_2d_interferences, 0)
        self.assertEqual(stage_b.call_count, 2)

    def test_invalid_preferred_order_fails_closed(self):
        result, _ = self.run_mocked(
            preferred_bend_order=("B01", "B01"),
            group_first=False,
        )
        self.assertEqual(result.status, PlanarSearchStatus.INVALID_INPUT)
        self.assertEqual(result.candidates, ())


if __name__ == "__main__":
    unittest.main()
