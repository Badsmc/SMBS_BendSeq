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
    validate_partial_planar_prefix,
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

    def test_real_occ_gauge_obstruction_is_blocking_not_candidate(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCADError
        def obstructed(*args, **kwargs):
            raise BackgaugeCADError(
                "OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT:"
                "finger=1 obstructing_component=panel:P05 "
                "volume_mm3=18963.980887 fixed=panel:P04 "
                "required_stop_s_mm=36.2601 hint_s_mm=11.2566 "
                "front_protrusion_mm=25.0 "
                "overlap_bbox_xyz_mm=[0,1,0,1,0,1]"
            )
        result, _ = self.run_mocked(
            obstructed, group_first=False, max_nodes=20,
            max_search_time_s=3.0,
        )
        self.assertEqual(result.max_depth_reached, 0)
        self.assertFalse(result.candidates)
        self.assertEqual(result.rejected_2d_interferences, 2)
        self.assertTrue(result.evaluated_step_details)
        self.assertTrue(all(
            step.evidence == "CAD_3D_INTERFERENCE"
            for _, step in result.evaluated_step_details
        ))
        self.assertEqual(
            result.to_dict()["per_bend_contact_summary"]["B01"]["interferences"],
            1
        )

    def test_unverified_initial_loading_does_not_exhaust_root_search(self):
        """An assumed 100mm loading start cannot prove a bend impossible."""
        from smbs_core.kinematics2d.sequence_search_2d import _classify_step
        finding = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D,
            "STAGE_B_LOADING_ROUTE_UNVERIFIED",
            "initial_cad_loading_route_intersection_at_assumed_start_not_bend_rejection",
            sampled_phases=("LOAD_TO_GAUGE", "APPROACH", "BENDING",
                            "BOTTOM", "RETRACT"),
            contact_statuses=("UNVERIFIED", "CLEAR", "CLEAR",
                              "CLEAR", "CLEAR"),
            backgauge_checked=True,
            diagnostic_findings=((
                "LOAD_TO_GAUGE", "BACKGAUGE_CAD_3D", "finger1:panel:P05",
                "INTERFERENCE", "material_overlap_mm3=19152.378444",
            ),),
        )
        self.assertEqual(_classify_step(finding), "UNVERIFIED_2D")
        result, _ = self.run_mocked(
            finding, group_first=False, max_nodes=20,
            max_search_time_s=3.0
        )
        self.assertGreater(result.max_depth_reached, 0)
        self.assertEqual(result.rejected_2d_interferences, 0)
        self.assertEqual(
            result.status, PlanarSearchStatus.UNVERIFIED_CANDIDATES
        )
        self.assertTrue(result.candidates)
        self.assertTrue(all(
            c.evidence != "SAMPLED_2D_CLEAR_OR_TOUCH"
            for c in result.candidates
        ))
        self.assertTrue(any(
            "LOAD_TO_GAUGE" in f
            for _, step in result.evaluated_step_details
            for f in (step.reason,)
        ) or any(
            "BACKGAUGE_CAD_3D" == finding[1]
            for _, step in result.evaluated_step_details
            for finding in step.diagnostic_findings
        ))

    def test_best_partial_eight_bends_stays_separate_from_full_candidates(self):
        names = tuple("B%02d" % n for n in range(1, 11))
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.session.bend_order = names

        def effect(state, bid, *_args, **_kwargs):
            if bid in ("B09", "B10") and any(state.angles.values()):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D",
                    "interference_after_prior_bend",
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        result, _ = self.run_mocked(
            effect, min_partial_bends=7,
            preferred_bend_order=names, group_first=False,
            max_nodes=150, max_search_time_s=3.0,
        )
        self.assertEqual(result.candidates, ())
        self.assertIsNotNone(result.partial_candidate)
        self.assertEqual(len(result.partial_candidate.sequence), 8)
        self.assertEqual(len(result.partial_remainder), 2)
        self.assertEqual(
            result.partial_candidate.evidence,
            "PARTIAL_COARSE_2D_NOT_FULLY_REPLAYED",
        )
        self.assertEqual(
            result.to_dict()["partial_remainder"],
            list(result.partial_remainder),
        )

    def test_conditional_request_does_not_accept_central_punch_interference(self):
        def collide(*_args, **_kwargs):
            return PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D,
                "STAGE_B_SECTION_REQUIRES_3D",
                "2d_section_finding_requires_3d_validation:APPROACH:P02:intersection",
                sampled_phases=("APPROACH",),
                contact_statuses=("INTERFERENCE",),
                diagnostic_findings=(
                    ("APPROACH", "PUNCH", "P02", "INTERFERENCE", "intersection"),
                ),
            )

        result, call = self.run_mocked(
            collide, allow_end_zone_collision=True, end_zone_length_mm=10.0
        )
        self.assertTrue(result.conditional_end_zone_requested)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.rejected_2d_interferences, 2)
        self.assertTrue(call.call_args.kwargs["allow_end_zone_collision"])

    def test_alternate_orientation_is_diagnostic_not_automatic_clearance(self):
        def screen(_state, bid, *_args, **kwargs):
            if bid == "B02" and not kwargs.get("reverse_axis", False):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D",
                    "normal_orientation_collision",
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        result, stage_b = self.run_mocked(
            screen, alternate_axis_bends=("B02",)
        )
        self.assertEqual(result.candidates, ())
        self.assertGreater(len(result.alternative_setup_trials), 0)
        self.assertTrue(any(t[3] == "SAMPLED_2D_CLEAR_OR_TOUCH"
                            for t in result.alternative_setup_trials))
        self.assertTrue(any(call.kwargs.get("reverse_axis", False)
                            for call in stage_b.call_args_list))

    def test_full_density_partial_gate_preserves_state_and_rejects_interference(self):
        original = (dict(self.state.angles), dict(self.state.transforms))
        with patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_a",
            return_value=PreliminaryScreeningReport(
                ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "ok",
            ),
        ), patch(
            "smbs_core.kinematics2d.sequence_search_2d.screen_candidate_stage_b",
            side_effect=[
                stage_b_result(),
                PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "collision",
                    contact_statuses=("INTERFERENCE",),
                ),
            ],
        ) as checker:
            ok, reason = validate_partial_planar_prefix(
                self.session, self.tooling, ("B01", "B02"),
            )
        self.assertFalse(ok)
        self.assertIn("B02:collision", reason)
        self.assertEqual(self.state.angles, original[0])
        self.assertEqual(self.state.transforms, original[1])
        self.assertEqual(checker.call_count, 2)
        self.assertEqual(len(checker.call_args.kwargs["fractions"]), 17)
        self.assertEqual(len(checker.call_args.kwargs["stroke_fractions"]), 9)

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

    def test_fair_root_probing_finds_later_root(self):
        names = ("B01", "B02", "B03")
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.session.bend_order = names

        def later_root_only(state, bend, *_args, **_kwargs):
            # B03 must be performed before any other bend.
            if bend == "B03" and any(
                state.angles.get(prev, 0) != 0 for prev in ("B01", "B02")
            ):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "blocked_after_other_bend",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        result, _ = self.run_mocked(
            later_root_only, preferred_bend_order=names,
            group_first=False, root_probe_nodes=3,
            max_complete_candidates=1,
        )
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual(result.candidates[0].sequence[0], "B03")
        self.assertGreaterEqual(result.deferred_root_probes, 0)

    def test_contact_summary_identifies_die_without_changing_search(self):
        def block_second(_state, bend, *_args, **_kwargs):
            if bend == "B02":
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D",
                    "2d_section_finding_requires_3d_validation:APPROACH:P02:proper_material_boundary_crossing",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                    diagnostic_findings=(
                        ("APPROACH", "DIE", "P02", "INTERFERENCE",
                         "proper_material_boundary_crossing"),
                        ("APPROACH", "DIE", "P02", "INTERFERENCE",
                         "proper_material_boundary_crossing"),
                    ),
                )
            return stage_b_result()

        result, _ = self.run_mocked(block_second)
        data = result.to_dict()
        self.assertEqual(result.status, PlanarSearchStatus.EXHAUSTED)
        summary = data["per_bend_contact_summary"]["B02"]
        self.assertEqual(summary["interferences"], 2)
        self.assertEqual(summary["clear_or_touch"], 0)
        self.assertEqual(summary["contact_findings"], [{
            "phase": "APPROACH", "tool": "DIE", "component": "P02",
            "reason": "proper_material_boundary_crossing", "transitions": 2,
        }])

    def test_mutual_first_step_obstruction_is_diagnostic_only(self):
        names = ("B01", "B02", "B03")
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.session.bend_order = names

        def screen(state, bend, *_args, **_kwargs):
            done = {name for name, angle in state.angles.items() if angle != 0}
            if (done == {"B01"} and bend == "B02"
                    or done == {"B02"} and bend == "B01"):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "pairwise_overlap",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        result, _ = self.run_mocked(screen, max_complete_candidates=20)
        paired = result.to_dict()["paired_first_step_obstructions"]
        self.assertEqual(len(paired), 1)
        self.assertEqual((paired[0]["first_bend"], paired[0]["second_bend"]),
                         ("B01", "B02"))
        self.assertEqual(paired[0]["after_first_reason"], "pairwise_overlap")
        # Later after B03 both can still be admissible; pairwise findings
        # must never prune future states or override candidate evidence.
        self.assertGreater(len(result.candidates), 0)
        self.assertTrue(any("B03" in c.sequence for c in result.candidates))

    def test_timed_root_probe_reaches_later_start_even_with_low_node_count(self):
        from time import sleep
        names = ("B01", "B02", "B03")
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.session.bend_order = names
        roots_seen = []

        def delayed_screen(state, bid, *_args, **_kwargs):
            sleep(0.004)
            if not any(state.angles.values()):
                roots_seen.append(bid)
            # To complete, the solver must start with B03.
            if bid == "B03" and any(
                state.angles.get(prev, 0) != 0 for prev in ("B01", "B02")
            ):
                return PreliminaryScreeningReport(
                    ScreeningDecision.CONTINUE_TO_3D,
                    "STAGE_B_SECTION_REQUIRES_3D", "bad_order",
                    sampled_phases=("APPROACH",),
                    contact_statuses=("INTERFERENCE",),
                )
            return stage_b_result()

        result, _ = self.run_mocked(
            delayed_screen, preferred_bend_order=names,
            group_first=False, root_probe_nodes=100,
            root_probe_seconds=0.002,
            max_search_time_s=3.0, max_complete_candidates=1,
        )
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertIn("B03", roots_seen)
        self.assertGreater(result.deferred_root_probes, 0)
        self.assertEqual(result.candidates[0].sequence[0], "B03")
        self.assertEqual(result.dead_state_cache_hits >= 0, True)

    def test_invalid_root_probe_seconds(self):
        for value in (-0.1, float("inf"), float("nan")):
            result, _ = self.run_mocked(root_probe_seconds=value)
            self.assertEqual(result.status, PlanarSearchStatus.INVALID_INPUT)

    def test_dead_subset_memo_never_overrides_replay(self):
        names = ("B01", "B02", "B03")
        self.state = DummyState(names)
        self.session.fold_state = self.state
        self.session.bend_order = names
        original_angles = dict(self.state.angles)
        result, _ = self.run_mocked(
            exploration_fractions=(0.0, 1.0),
            preferred_bend_order=("B03", "B02", "B01"),
            group_first=False,
            root_probe_nodes=2,
            max_complete_candidates=1,
        )
        self.assertEqual(result.status, PlanarSearchStatus.SAMPLED_2D_CANDIDATES)
        self.assertEqual(len(result.candidates[0].sequence), 3)
        self.assertEqual(result.final_replay_attempts, 1)
        self.assertEqual(self.state.angles, original_angles)
        self.assertIn("dead_state_cache_hits", result.to_dict())
        self.assertIn("deferred_root_probes", result.to_dict())


if __name__ == "__main__":
    unittest.main()
