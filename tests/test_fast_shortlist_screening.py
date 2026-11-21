"""Regression checks for bounded advisory FAST Stage A/B shortlist screening.

The mocked probe models its decision at a particular completed-angle state;
no FreeCAD or tool profiles are needed to exercise the scheduler.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from time import perf_counter
import unittest

from smbs_core.kinematics2d.fast_bfm_beam_search import FastCandidate
from smbs_core.kinematics2d.fast_shortlist_screening import (
    _probe_existing_stage_b, screen_fast_shortlist,
)
from smbs_core.kinematics2d.staged_bends import BendOperation


class FakeFoldState:
    def __init__(self):
        self.angles = {"B01": 13.0, "B02": 26.0}
        self.transforms = {"original_pose": "retained"}

    def reset_all(self):
        self.angles.update({"B01": 0.0, "B02": 0.0})
        self.transforms.clear()
        self.transforms["current_pose"] = "reset"

    def set_angle(self, bend_id, angle):
        self.angles[bend_id] = float(angle)


def candidate(order, cost=0.0, reverse=None):
    reverse = reverse or {}
    return FastCandidate(
        tuple(order),
        tuple((oid, bool(reverse.get(oid, False))) for oid in order),
        float(cost),
        0.0,
    )


class FastShortlistScreeningTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeFoldState()
        self.steps = (
            BendOperation("B01", "B01", 90.0),
            BendOperation("B02", "B02", 90.0),
        )
        self.outlines = {
            "P0": ((0., 0.), (2., 0.), (2., 2.)),
        }
        self.tools = SimpleNamespace(punch=object(), die=object())

    def screen(self, plans, probe, **kwargs):
        return screen_fast_shortlist(
            self.state, self.steps, plans, self.outlines,
            self.tools, probe=probe, **kwargs,
        )

    def test_2d_interference_ranks_below_clean_candidate_without_pruning(self):
        def probe(state, op, reverse, tools, vertices, deadline):
            if op.bend_id == "B01" and state.angles["B02"] == 90.0:
                return "POTENTIAL_2D_INTERFERENCE", "folded_flange_hits_punch"
            return "SAMPLED_2D_TOOLS_GAUGE_CLEAR", "sampled_no_interference"

        plans = (
            candidate(("B02", "B01"), cost=1.0),
            candidate(("B01", "B02"), cost=5.0),
        )
        report = self.screen(plans, probe)
        self.assertEqual(report.ranked_indices, (1, 0))
        self.assertEqual(len(report.checks), 2)
        self.assertEqual(report.checks[0].status, "POTENTIAL_2D_INTERFERENCE")
        self.assertEqual(report.checks[0].clear_prefix_steps, 1)
        self.assertEqual(report.checks[1].status, "SAMPLED_2D_TOOLS_GAUGE_CLEAR")
        self.assertEqual(report.checks[1].clear_prefix_steps, 2)
        self.assertIn("B01:folded_flange_hits_punch", report.checks[0].first_issue)
        self.assertEqual(self.state.angles, {"B01": 13., "B02": 26.})
        self.assertEqual(self.state.transforms, {"original_pose": "retained"})
        self.assertEqual(
            report.to_dict()["checks"][0]["evidence"],
            "ADVISORY_SAMPLED_2D_NOT_3D_VERIFIED",
        )

    def test_equal_2d_obstruction_prefers_longer_clear_prefix_over_low_cost(self):
        # An inexpensive candidate that fails at its first bend should not
        # outrank an expensive candidate reaching the second bend.
        plans = (
            candidate(("B01", "B02"), cost=1.0),
            candidate(("B02", "B01"), cost=100.0),
        )

        def probe(state, op, reverse, tools, vertices, deadline):
            if op.bend_id == "B01":
                return "POTENTIAL_2D_INTERFERENCE", "B01_crossing"
            return "SAMPLED_2D_TOOLS_ONLY", "sampled"

        report = self.screen(plans, probe)
        self.assertEqual(report.checks[0].clear_prefix_steps, 0)
        self.assertEqual(report.checks[1].clear_prefix_steps, 1)
        self.assertEqual(report.ranked_indices, (1, 0))

    def test_tool_collision_metadata_prefers_already_formed_other_panel(self):
        from smbs_core.kinematics2d.preliminary_screening import (
            PreliminaryScreeningReport, ScreeningDecision,
        )
        stage_a = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D, "STAGE_A_PASSED", "inputs_valid",
        )
        stage_b = PreliminaryScreeningReport(
            ScreeningDecision.CONTINUE_TO_3D,
            "STAGE_B_SECTION_REQUIRES_3D",
            "2d_section_finding_requires_3d_validation:BENDING:P09:"
            "proper_material_boundary_crossing",
            diagnostic_findings=(
                ("BENDING","PUNCH","active_fixed","INTERFERENCE",
                 "proper_material_boundary_crossing"),
                ("BENDING","PUNCH","P09","INTERFERENCE",
                 "proper_material_boundary_crossing"),
            ),
        )
        with patch(
            "smbs_core.kinematics2d.preliminary_screening.screen_candidate_stage_a",
            return_value=stage_a,
        ), patch(
            "smbs_core.kinematics2d.preliminary_screening.screen_candidate_stage_b",
            return_value=stage_b,
        ):
            status, reason = _probe_existing_stage_b(
                self.state, self.steps[0], False, self.tools,
                self.outlines, perf_counter()+10,
            )
        self.assertEqual(status, "POTENTIAL_2D_INTERFERENCE")
        self.assertIn("tool=PUNCH", reason)
        self.assertIn("phase=BENDING", reason)
        self.assertIn("component=P09", reason)
        self.assertIn("contact=proper_material_boundary_crossing", reason)

    def test_cache_reuses_same_folded_subset_bend_and_orientation(self):
        calls = []

        def probe(state, op, reverse, tools, vertices, deadline):
            calls.append((op.bend_id, reverse, tuple(sorted(state.angles.items()))))
            return "SAMPLED_2D_TOOLS_GAUGE_CLEAR", "sampled_clear"

        plans = (
            candidate(("B01", "B02")),
            candidate(("B01", "B02"), cost=3.0),
        )
        report = self.screen(plans, probe)
        self.assertEqual(report.actual_probes, 2)
        self.assertEqual(report.cache_hits, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report.checks[1].cached_steps, 2)

    def test_cache_does_not_merge_opposite_180_orientation(self):
        visited = []

        def probe(state, op, reverse, tools, vertices, deadline):
            visited.append((op.bend_id, reverse))
            return "SAMPLED_2D_TOOLS_ONLY", "rear_gauge_unverified"

        plans = (
            candidate(("B01", "B02")),
            candidate(("B01", "B02"), reverse={"B01": True}),
        )
        report = self.screen(plans, probe)
        self.assertIn(("B01", False), visited)
        self.assertIn(("B01", True), visited)
        self.assertEqual(report.actual_probes, 3)
        self.assertEqual(report.cache_hits, 1)
        self.assertEqual(report.checks[0].status, "SAMPLED_2D_TOOLS_ONLY")

    def test_failing_probe_is_unverified_and_restores_state(self):
        def probe(*args):
            raise RuntimeError("section unavailable")

        report = self.screen((candidate(("B01", "B02")),), probe)
        self.assertEqual(report.checks[0].status, "PARTIAL_UNVERIFIED")
        self.assertIn("RuntimeError", report.checks[0].first_issue)
        self.assertEqual(self.state.angles["B01"], 13.0)
        self.assertEqual(self.state.transforms["original_pose"], "retained")

    def test_budget_exceeded_transition_retries_on_second_pass(self):
        attempts = []
        cached = {}

        def probe(*args):
            attempts.append("probe")
            return (("BUDGET_EXCEEDED", "2d_probe_time_budget_reached")
                    if len(attempts) == 1 else
                    ("SAMPLED_2D_TOOLS_ONLY", "sampled_without_gauge"))

        plans = (candidate(("B01", "B02")),)
        first = self.screen(plans, probe, transition_cache=cached)
        self.assertEqual(first.checks[0].status, "BUDGET_EXCEEDED")
        self.assertEqual(first.actual_probes, 1)
        self.assertEqual(cached, {})
        second = self.screen(plans, probe, transition_cache=cached)
        self.assertEqual(second.checks[0].status, "SAMPLED_2D_TOOLS_ONLY")
        self.assertEqual(second.actual_probes, 2)
        self.assertEqual(second.cache_hits, 0)
        self.assertEqual(len(cached), 2)

    def test_priority_checks_novel_plan_first_but_keeps_original_indices(self):
        observed_first_operations = []

        def probe(state, op, reverse, tools, vertices, deadline):
            if all(angle == 0.0 for angle in state.angles.values()):
                observed_first_operations.append(op.bend_id)
            return "SAMPLED_2D_TOOLS_ONLY", "sampled_without_gauge"

        plans = (
            candidate(("B01", "B02"), cost=1.),
            candidate(("B02", "B01"), cost=2.),
        )
        report = self.screen(
            plans, probe, priority_candidate_indices=(1,),
        )
        self.assertEqual(observed_first_operations, ["B02", "B01"])
        self.assertEqual(report.checks[0].operation_ids, plans[0].operation_ids)
        self.assertEqual(report.checks[1].operation_ids, plans[1].operation_ids)
        self.assertEqual(report.ranked_indices, (0, 1))

    def test_existing_cached_clear_remains_valid_after_new_plan_exhausts_budget(self):
        cached = {}
        old = candidate(("B01", "B02"))
        fresh = candidate(("B01", "B02"), reverse={"B01": True})
        first = self.screen(
            (old,),
            lambda *args: ("SAMPLED_2D_TOOLS_ONLY", "sampled_tools"),
            transition_cache=cached,
        )
        self.assertEqual(first.checks[0].status, "SAMPLED_2D_TOOLS_ONLY")
        self.assertEqual(len(cached), 2)

        class Clock:
            tick = 0
            def __call__(self):
                self.tick += 1
                return self.tick * 0.05

        report = self.screen(
            (old, fresh),
            lambda *args: self.fail("Expired budget must not run geometry"),
            transition_cache=cached,
            priority_candidate_indices=(1,),
            clock=Clock(),
            budget_seconds=0.04,
        )
        self.assertEqual(report.checks[1].status, "BUDGET_EXCEEDED")
        self.assertEqual(report.checks[0].status, "SAMPLED_2D_TOOLS_ONLY")
        self.assertEqual(report.checks[0].clear_prefix_steps, 2)
        self.assertEqual(report.actual_probes, 0)
        self.assertEqual(report.cache_hits, 2)

    def test_first_interference_records_exact_completed_mask_and_pose(self):
        def probe(state, op, reverse, tools, vertices, deadline):
            if op.operation_id == "B02" and state.angles["B01"] == 90:
                return "POTENTIAL_2D_INTERFERENCE", "P09_punch_overlap"
            return "SAMPLED_2D_TOOLS_ONLY", "sampled"

        report = self.screen(
            (candidate(("B01", "B02"), reverse={"B02": True}),), probe,
        )
        check = report.checks[0]
        self.assertEqual(check.status, "POTENTIAL_2D_INTERFERENCE")
        self.assertEqual(check.failed_transition, (1, "B02", True))
        self.assertEqual(check.clear_prefix_steps, 1)
        self.assertEqual(check.to_dict()["clear_prefix_steps"], 1)
        self.assertEqual(
            check.to_dict()["failed_transition"]["completed_mask"], 1
        )

    def test_stage_aware_skip_does_not_fake_prebend_clearance(self):
        staged = (
            BendOperation("B01.1", "B01", 30.0, "PREBEND"),
            BendOperation("B01.2", "B01", 90.0, "FINISH"),
            BendOperation("B02", "B02", 90.0),
        )
        result = screen_fast_shortlist(
            self.state, staged,
            (candidate(("B01.1", "B02", "B01.2")),),
            self.outlines, self.tools,
            probe=lambda *args: self.fail("Stage B cannot verify a partial bend"),
        )
        self.assertEqual(result.checks[0].status, "STAGED_UNSUPPORTED")

    def test_absent_tooling_is_not_reported_as_clear(self):
        result = screen_fast_shortlist(
            self.state, self.steps,
            (candidate(("B01", "B02")),),
            self.outlines, None,
        )
        self.assertEqual(result.checks[0].status, "NO_TOOLING")
        self.assertEqual(result.actual_probes, 0)

    def test_expired_time_budget_never_gives_a_clear_result(self):
        class Clock:
            tick = 0.
            def __call__(self):
                self.tick += 0.06
                return self.tick

        result = self.screen(
            (candidate(("B01", "B02")),),
            lambda *args: self.fail("Should not start after deadline"),
            clock=Clock(),
            budget_seconds=0.05,
        )
        self.assertEqual(result.checks[0].status, "BUDGET_EXCEEDED")
        self.assertEqual(result.actual_probes, 0)


if __name__ == "__main__":
    unittest.main()
