"""Regression: pre-bend and finish-bend must have independent 180° settings."""
from __future__ import annotations

import unittest

from smbs_core.kinematics2d.press_cycle import PressCycleController
from smbs_core.kinematics2d.staged_orientation import (
    expanded_operation_orientations, reverse_axis_for_operation,
)
from test_staged_bends import controller


class IndependentOperationOrientationTests(unittest.TestCase):
    def setUp(self):
        self.sequence = controller()
        self.sequence.add_prebend("B01", 35.0)
        self.sequence.reorder_operations(
            ("B01.1", "B02", "B03", "B01.2")
        )

    def _press_cycle(self, choices):
        cycle = object.__new__(PressCycleController)
        cycle.sequence = self.sequence
        cycle.loading_orientation_by_bend = choices
        return cycle

    def test_prebend_checkbox_does_not_flip_finish_checkbox(self):
        physical = {"B01": False, "B02": False, "B03": False}
        overrides = {"B01.1": True}
        choices = expanded_operation_orientations(
            physical, self.sequence.operation_steps, overrides
        )
        self.assertTrue(reverse_axis_for_operation(choices, "B01", "B01.1"))
        self.assertFalse(reverse_axis_for_operation(choices, "B01", "B01.2"))
        cycle = self._press_cycle(choices)
        self.assertTrue(cycle._loading_reverse_axis_for_step(0))
        self.assertFalse(cycle._loading_reverse_axis_for_step(3))
        self.assertFalse(cycle._loading_reverse_axis_for_step(1))

    def test_finish_checkbox_does_not_flip_prebend_checkbox(self):
        physical = {"B01": False, "B02": True, "B03": False}
        choices = expanded_operation_orientations(
            physical, self.sequence.operation_steps, {"B01.2": True}
        )
        cycle = self._press_cycle(choices)
        self.assertFalse(cycle._loading_reverse_axis_for_step(0))
        self.assertTrue(cycle._loading_reverse_axis_for_step(3))
        self.assertTrue(cycle._loading_reverse_axis_for_step(1))

    def test_legacy_physical_orientation_is_independent_override_fallback(self):
        physical = {"B01": True, "B02": False, "B03": False}
        choices = expanded_operation_orientations(
            physical, self.sequence.operation_steps, {"B01.1": False}
        )
        cycle = self._press_cycle(choices)
        self.assertFalse(cycle._loading_reverse_axis_for_step(0))
        self.assertTrue(cycle._loading_reverse_axis_for_step(3))

    def test_reorder_keeps_orientation_bound_to_operation_id(self):
        overrides = {"B01.1": True, "B01.2": False}
        physical = {"B01": False, "B02": False, "B03": False}
        self.sequence.reorder_operations(
            ("B03", "B01.1", "B01.2", "B02")
        )
        cycle = self._press_cycle(expanded_operation_orientations(
            physical, self.sequence.operation_steps, overrides
        ))
        self.assertFalse(cycle._loading_reverse_axis_for_step(0))
        self.assertTrue(cycle._loading_reverse_axis_for_step(1))
        self.assertFalse(cycle._loading_reverse_axis_for_step(2))

    def test_legacy_unstaged_sequence_still_uses_bend_id(self):
        sequence = controller()
        cycle = object.__new__(PressCycleController)
        cycle.sequence = sequence
        cycle.loading_orientation_by_bend = {
            "B01": True, "B02": False, "B03": True
        }
        self.assertTrue(cycle._loading_reverse_axis_for_step(0))
        self.assertFalse(cycle._loading_reverse_axis_for_step(1))


if __name__ == "__main__":
    unittest.main()
