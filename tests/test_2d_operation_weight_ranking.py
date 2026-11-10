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

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            OperationWeights(flip=-1)


if __name__ == "__main__":
    unittest.main()
