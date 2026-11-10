"""Regression checks for SMBS 2D technological scoring and ranking metadata."""
import unittest

from smbs_core.kinematics2d.operation_cost import (
    OperationWeights, WorkpieceMachineSetup, score_sequence,
)
from smbs_core.kinematics2d.sequence_search_2d import PlanarCandidate


def setup(bend, rotation=0.0, flipped=False, length=100.0):
    return WorkpieceMachineSetup(
        bend_id=bend, rotation_deg=rotation, flipped=flipped,
        shift_xm_mm=0.0, bend_length_mm=length,
    )


class OperationWeightRankingTests(unittest.TestCase):
    def test_weights_change_preferred_candidate(self):
        # A: one overturn, no rotation; B: 90-degree rotation, no overturn.
        a = [setup("B01"), setup("B02", flipped=True)]
        b = [setup("B01"), setup("B02", rotation=90)]
        order = ("B01", "B02")
        prioritize_flips = OperationWeights(
            flip=100, turn_fixed=1, turn_per_90_deg=1)
        prioritize_turns = OperationWeights(
            flip=1, turn_fixed=100, turn_per_90_deg=10)
        self.assertGreater(
            score_sequence(order, a, prioritize_flips).total,
            score_sequence(order, b, prioritize_flips).total)
        self.assertLess(
            score_sequence(order, a, prioritize_turns).total,
            score_sequence(order, b, prioritize_turns).total)

    def test_first_operation_has_no_changeover_penalty(self):
        p = score_sequence(("B01",), [setup("B01", rotation=180, flipped=True)])
        self.assertEqual(p.total, 0.0)
        self.assertTrue(p.is_complete)

    def test_cost_breakdown_serializable(self):
        weights = OperationWeights()
        score = score_sequence(
            ("B01", "B02"),
            [setup("B01"), setup("B02", rotation=90)],
            weights,
        )
        candidate = PlanarCandidate(
            ("B01", "B02"), (), "SAMPLED_2D_CLEAR_OR_TOUCH",
            score.total, score.is_complete,
            cost_breakdown=tuple(s.to_dict() for s in score.steps),
        )
        artifact = candidate.to_dict()
        self.assertEqual(len(artifact["cost_breakdown"]), 2)
        self.assertEqual(artifact["cost_breakdown"][0]["total"], 0.0)
        self.assertEqual(artifact["cost_breakdown"][1]["total"], score.total)

    def test_punch_and_die_length_costs_independent(self):
        w = OperationWeights(
            length_change_fixed=0, length_delta_per_100_mm=0,
            punch_length_change_fixed=10,
            punch_length_delta_per_100_mm=2,
            die_length_change_fixed=5,
            die_length_delta_per_100_mm=1,
        )
        common = dict(rotation_deg=0, flipped=False,
                      shift_xm_mm=0, bend_length_mm=100)
        first = WorkpieceMachineSetup(
            bend_id="B01", punch_length_mm=100, die_length_mm=200,
            punch_length_source="VIRTUAL_MATCH_BEND",
            die_length_source="VIRTUAL_MATCH_BEND", **common)
        next_ = WorkpieceMachineSetup(
            bend_id="B02", punch_length_mm=200, die_length_mm=300,
            punch_length_source="VIRTUAL_MATCH_BEND",
            die_length_source="VIRTUAL_MATCH_BEND", **common)
        step = score_sequence(("B01", "B02"), (first, next_), w).steps[1]
        self.assertEqual(step.punch_length_delta_mm, 100)
        self.assertEqual(step.die_length_delta_mm, 100)
        self.assertEqual(step.punch_length_cost, 12)
        self.assertEqual(step.die_length_cost, 6)
        self.assertEqual(step.total, 18)
        self.assertFalse(step.tooling_lengths_from_fixed_profiles)

    def test_fixed_tooling_length_does_not_follow_bend_line(self):
        w = OperationWeights(
            length_change_fixed=2, length_delta_per_100_mm=0,
            punch_length_change_fixed=50, die_length_change_fixed=50,
        )
        a = WorkpieceMachineSetup(
            "B01", 0, False, 0, 100,
            punch_length_mm=500, die_length_mm=500,
            punch_length_source="FIXED_PROFILE",
            die_length_source="FIXED_PROFILE",
        )
        b = WorkpieceMachineSetup(
            "B02", 0, False, 0, 200,
            punch_length_mm=500, die_length_mm=500,
            punch_length_source="FIXED_PROFILE",
            die_length_source="FIXED_PROFILE",
        )
        step = score_sequence(("B01", "B02"), (a, b), w).steps[1]
        self.assertEqual(step.length_change_cost, 2)
        self.assertEqual(step.punch_length_cost, 0)
        self.assertEqual(step.die_length_cost, 0)
        self.assertTrue(step.tooling_lengths_from_fixed_profiles)

    def test_missing_tool_length_is_not_invented(self):
        a = setup("B01")
        b = setup("B02", length=300)
        step = score_sequence(("B01", "B02"), (a, b)).steps[1]
        self.assertIsNone(step.punch_length_delta_mm)
        self.assertIsNone(step.die_length_delta_mm)
        self.assertEqual(step.punch_length_cost, 0.0)
        self.assertEqual(step.die_length_cost, 0.0)
        self.assertFalse(step.tooling_lengths_from_fixed_profiles)

    def test_search_virtual_lengths_use_bend_length_not_stale_tooling_flag(self):
        from types import SimpleNamespace
        from smbs_core.kinematics2d.sequence_search_2d import (
            _setup_with_scored_tool_lengths,
        )
        # GUI's tooling flag may still be False while the search-stage override
        # match_active_bend_length=True. Both length scoring and Stage A/B must
        # use the same selected mode.
        tooling = SimpleNamespace(
            match_active_bend_length=False,
            punch=SimpleNamespace(length_mm=100.0),
            die=SimpleNamespace(length_mm=100.0),
        )
        first = _setup_with_scored_tool_lengths(
            setup("B01", length=293.0), tooling, True)
        second = _setup_with_scored_tool_lengths(
            setup("B02", length=498.18), tooling, True)
        self.assertEqual(first.punch_length_mm, 293.0)
        self.assertEqual(second.die_length_mm, 498.18)
        self.assertEqual(first.punch_length_source, "VIRTUAL_MATCH_BEND")
        self.assertEqual(second.die_length_source, "VIRTUAL_MATCH_BEND")
        penalty = score_sequence(("B01", "B02"), (first, second)).steps[1]
        self.assertGreater(penalty.punch_length_cost, 0)
        self.assertGreater(penalty.die_length_cost, 0)

    def test_search_fixed_lengths_stay_constant_when_bend_changes(self):
        from types import SimpleNamespace
        from smbs_core.kinematics2d.sequence_search_2d import (
            _setup_with_scored_tool_lengths,
        )
        tooling = SimpleNamespace(
            match_active_bend_length=True,
            punch=SimpleNamespace(length_mm=500.0),
            die=SimpleNamespace(length_mm=500.0),
        )
        first = _setup_with_scored_tool_lengths(
            setup("B01", length=293.0), tooling, False)
        second = _setup_with_scored_tool_lengths(
            setup("B02", length=498.18), tooling, False)
        self.assertEqual(first.punch_length_mm, 500)
        self.assertEqual(second.die_length_mm, 500)
        self.assertEqual(first.punch_length_source, "FIXED_PROFILE")
        penalty = score_sequence(("B01", "B02"), (first, second)).steps[1]
        self.assertEqual(penalty.punch_length_cost, 0)
        self.assertEqual(penalty.die_length_cost, 0)

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            OperationWeights(flip=-1)


if __name__ == "__main__":
    unittest.main()
