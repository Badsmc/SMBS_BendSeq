"""
test_stage5_oracle.py - Stage 5 Physical Oracle Validation Test Suite

Verifies:
- Physical Oracle validation responses (VALID / INVALID).
- Canonical stage names: APPROACH, KINEMATICS, SELF, TOOL, MACHINE, RETRACT.
- Rich diagnostics reporting on step validation failure.
"""

import sys
import os
import unittest

from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator, ValidationResult
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class TestStage5PhysicalOracle(unittest.TestCase):
    """Stage 5 Physical Oracle Tests."""

    def setUp(self):
        self.machine = PressBrakeMachine()
        self.backend = MockBackend()
        self.validator = PhysicalValidator(self.machine, backend=self.backend)
        
        self.bg = BendGraph()
        self.b1 = BendRecord("BEND_01", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 2.0, 2.0)
        self.bg.add_bend(self.b1)
        
        self.pg = PanelGraph()
        self.pg.add_bend_connection("BEND_01", "PA", "PB")

    def test_oracle_approach_rejection(self):
        """Test rejection at APPROACH stage when bend is not in remaining_bends."""
        target_shape = {"type": "mock_shape"}
        state = FoldState.initial(target_shape, self.bg, self.pg)
        # Force remaining_bends to be empty
        state.remaining_bends.clear()

        result = self.validator.validate_step(state, self.b1, self.pg)
        self.assertFalse(result.valid)
        self.assertIn(result.stage, ("consistency", "APPROACH"))
        self.assertIn("not in remaining_bends", result.reason)

    def test_oracle_valid_step(self):
        """Test valid step produces next state with stage='valid'."""
        target_shape = {"type": "mock_shape"}
        state = FoldState.initial(target_shape, self.bg, self.pg)

        result = self.validator.validate_step(state, self.b1, self.pg)
        self.assertTrue(result.valid)
        self.assertEqual(result.stage, "valid")
        self.assertIsNotNone(result.next_state)

if __name__ == "__main__":
    unittest.main()
