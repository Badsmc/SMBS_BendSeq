"""Regression tests for independent backgauge Z change penalties."""

import unittest

from smbs_core.kinematics2d.operation_cost import (
    OperationWeights,
    WorkpieceMachineSetup,
    transition_penalty,
    score_sequence,
)


def setup(bend_id, s=100.0, z=0.0):
    return WorkpieceMachineSetup(
        bend_id=bend_id,
        rotation_deg=0.0,
        flipped=False,
        shift_xm_mm=0.0,
        bend_length_mm=100.0,
        backgauge_s_mm=s,
        backgauge_z_mm=z,
    )


class BackgaugeHeightCostTests(unittest.TestCase):
    def test_vertical_change_has_strong_independent_penalty(self):
        step = transition_penalty(setup("B01"), setup("B02", z=10.0))
        self.assertAlmostEqual(step.backgauge_height_delta_mm, 10.0)
        self.assertAlmostEqual(step.backgauge_height_cost, 14.0)
        self.assertAlmostEqual(step.backgauge_move_cost, 0.0)
        self.assertAlmostEqual(step.total, 14.0)
        self.assertEqual(step.to_dict()["backgauge_height_cost"], 14.0)

    def test_horizontal_movement_keeps_existing_penalty(self):
        step = transition_penalty(setup("B01"), setup("B02", s=200.0))
        self.assertAlmostEqual(step.backgauge_move_cost, 2.5)
        self.assertAlmostEqual(step.backgauge_height_cost, 0.0)

    def test_small_vertical_jitter_is_ignored(self):
        step = transition_penalty(setup("B01"), setup("B02", z=0.4))
        self.assertEqual(step.backgauge_height_cost, 0.0)

    def test_vertical_change_is_counted_in_sequence_total(self):
        score = score_sequence(
            ["B01", "B02", "B03"],
            [setup("B01"), setup("B02", z=10.0), setup("B03", z=10.0)],
        )
        self.assertAlmostEqual(score.total, 14.0)

    def test_weights_are_configurable_and_validated(self):
        weights = OperationWeights(backgauge_height_change_fixed=20.0)
        step = transition_penalty(setup("B01"), setup("B02", z=10.0), weights)
        self.assertAlmostEqual(step.backgauge_height_cost, 22.0)
        self.assertRaises(ValueError, OperationWeights, backgauge_height_change_fixed=-1.0)

    def test_unknown_gauge_is_not_misreported_as_zero_displacement(self):
        unknown = WorkpieceMachineSetup(
            bend_id="B01", rotation_deg=0, flipped=False,
            shift_xm_mm=0, bend_length_mm=100,
        )
        step = transition_penalty(unknown, setup("B02", z=10.0))
        self.assertIsNone(step.backgauge_height_delta_mm)
        self.assertFalse(step.backgauge_position_known)


if __name__ == "__main__":
    unittest.main()
