"""Operator-side loading preference cost regression tests."""
import unittest

from smbs_core.kinematics2d.operation_cost import (
    OperationWeights, WorkpieceMachineSetup, score_sequence,
    transition_penalty,
)


def setup(name, operator, rear):
    return WorkpieceMachineSetup(
        bend_id=name, rotation_deg=0.0, flipped=False,
        shift_xm_mm=0.0, bend_length_mm=100.0,
        operator_reach_mm=operator, rear_reach_mm=rear,
    )


class OperatorSidePreferenceTests(unittest.TestCase):
    def test_12mm_operator_800mm_rear_is_penalized(self):
        step = transition_penalty(None, setup("B01", 12, 800))
        self.assertAlmostEqual(step.operator_side_cost, 10 + 3 * 788 / 100)
        self.assertAlmostEqual(step.total, step.operator_side_cost)

    def test_long_operator_and_short_rear_are_preferred(self):
        step = transition_penalty(None, setup("B01", 800, 12))
        self.assertEqual(step.operator_side_cost, 0.0)

    def test_equal_sides_do_not_trigger_penalty(self):
        self.assertEqual(
            transition_penalty(None, setup("B01", 200, 200)).operator_side_cost,
            0.0,
        )

    def test_penalty_applies_to_every_bend_not_only_transitions(self):
        score = score_sequence(
            ("B01", "B02"), (setup("B01", 12, 800), setup("B02", 12, 800))
        )
        self.assertEqual(len(score.steps), 2)
        self.assertGreater(score.steps[0].operator_side_cost, 0)
        self.assertGreater(score.steps[1].operator_side_cost, 0)

    def test_missing_geometry_not_pretended_as_proven_ratio(self):
        item = WorkpieceMachineSetup(
            "B01", 0.0, False, 0.0, 100.0
        )
        self.assertEqual(transition_penalty(None, item).operator_side_cost, 0.0)
        self.assertIsNone(item.operator_reach_mm)

    def test_custom_weight_zero_disables_preference(self):
        weights = OperationWeights(operator_short_side_fixed=0.0,
                                   operator_short_side_per_100_mm=0.0)
        self.assertEqual(
            transition_penalty(None, setup("B01", 12, 800), weights).total, 0.0
        )

    def test_invalid_weight_rejected(self):
        with self.assertRaises(ValueError):
            OperationWeights(operator_short_side_fixed=-1.0)


if __name__ == "__main__":
    unittest.main()
