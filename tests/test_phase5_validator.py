"""
test_phase5_validator.py - Phase 5 Physical Oracle Test Suite

Verifies LAW-1 Physical Oracle requirements:
- PhysicalValidator is the single authoritative gate returning ValidationResult(valid=True/False).
- Evaluation pipeline order (consistency -> kinematics -> self -> tool -> machine -> trajectory -> valid).
- ValidationResult contains stage name and next FoldState on success.
"""

import sys
import os
import unittest

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from smbs_core.Core.bend_graph import BendRecord, BendGraph
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.fold_state import FoldState
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator, ValidationResult


class TestPhase5PhysicalOracle(unittest.TestCase):
    """Phase 5 Physical Oracle & Validation Gate Tests."""

    def setUp(self):
        from smbs_core.models.tooling import GeometryStatus
        self.machine = PressBrakeMachine()
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        self.validator = PhysicalValidator(self.machine, MockBackend())
        # Configure tooling as REAL with mock shapes for isolated unit testing of the validation pipeline
        if self.validator.detector.assembly:
            if self.validator.detector.assembly.die:
                self.validator.detector.assembly.die.geometry_status = GeometryStatus.REAL
            if self.validator.detector.assembly.punch:
                self.validator.detector.assembly.punch.geometry_status = GeometryStatus.REAL
            self.validator.detector.assembly.get_world_shape = lambda kind: {"mock": kind.value}

        self.bend_graph = BendGraph()
        self.b1 = BendRecord("BEND_01", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 2.0, 2.0)
        self.bend_graph.add_bend(self.b1)

        self.panel_graph = PanelGraph()
        self.panel_graph.add_bend_connection("BEND_01", "PA", "PB")
        self.panel_graph.panels["PA"].face_indices = ["F1"]
        self.panel_graph.panels["PB"].face_indices = ["F2"]
        mock_shape = {"type": "mock_box", "dim": (100.0, 50.0, 2.0)}
        self.root_state = FoldState.initial(mock_shape, self.bend_graph, self.panel_graph)

    def test_valid_step_validation(self):
        """Test validating a valid candidate step returns VALID result with next FoldState."""
        res = self.validator.validate_step(self.root_state, self.b1, self.panel_graph)
        self.assertTrue(res.valid)
        self.assertEqual(res.stage, "valid")
        self.assertIsNotNone(res.next_state)
        self.assertTrue(res.next_state.is_goal())

    def test_invalid_consistency_step(self):
        """Test validating a bend not in remaining_bends fails at consistency stage."""
        already_done_state = FoldState(shape=None, folded_bends=set(), remaining_bends=set())
        res = self.validator.validate_step(already_done_state, self.b1, self.panel_graph)
        self.assertFalse(res.valid)
        self.assertEqual(res.stage, "consistency")
        self.assertIn("not in remaining_bends", res.reason)


if __name__ == "__main__":
    unittest.main()
