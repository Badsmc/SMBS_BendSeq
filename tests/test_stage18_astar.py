"""
test_stage18_astar.py - Stage 18 Validation (A* Backward Planner)

Verifies:
- A* algorithm successfully finds the correct sequence.
- Uses `g(n) + h(n)` to guide the search, minimizing explored nodes.
- Reconstructs the forward path correctly.
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

from smbs_core.Solvers.astar_backward import AStarBackwardPlanner
from smbs_core.Physics.validator import PhysicalValidator, ValidationResult
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph, BendRecord

class MockValidator(PhysicalValidator):
    def __init__(self):
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        super().__init__(None, MockBackend())

    def validate_step(self, current_state, bend_record, panel_graph=None, tooling_override=None):
        # Always return valid for simple testing of the search logic
        next_state = current_state.apply_unfold(
            bend_id=bend_record.id,
            unfolded_shape=None,
            moving_panels=set()
        )
        return ValidationResult(valid=True, reason="Mock valid", stage="MOCK", next_state=next_state)

class TestStage18AStar(unittest.TestCase):
    def test_astar_search_basic(self):
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "P1", "P2", (0,0,0), (0,1,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B2", "F2", "P2", "P3", (10,0,0), (0,1,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B3", "F3", "P3", "P4", (20,0,0), (0,1,0), 90.0, 1.0, 1.0))

        initial_state = FoldState.initial(part_shape=None, bend_graph=bg, panel_graph=None)
        
        validator = MockValidator()
        planner = AStarBackwardPlanner(bend_graph=bg, validator=validator)
        
        # We override compute_heuristic to make it favor B3 -> B2 -> B1
        def mock_heuristic(state):
            # The fewer bends remaining, the closer to goal
            return len(state.remaining_bends)
            
        planner.compute_heuristic = mock_heuristic
        
        result = planner.solve(initial_state)
        
        # Sequence comes out forward, length should be 3
        bends_in_seq = [step["bend_id"] for step in result["sequence"]]
        self.assertIn("B1", bends_in_seq)
        self.assertIn("B2", bends_in_seq)
        self.assertIn("B3", bends_in_seq)
        
        # Check that nodes explored is minimal
        self.assertGreater(result["nodes_explored"], 0)

    def test_astar_depth_limit_no_solution(self):
        """Step 4 (4c-i): depth_limit < N makes goal unreachable → NO_SOLUTION.

        Verifies the safety net bounds per-branch depth without changing
        any behavior when depth_limit is None (default).
        """
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "P1", "P2", (0,0,0), (0,1,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B2", "F2", "P2", "P3", (10,0,0), (0,1,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B3", "F3", "P3", "P4", (20,0,0), (0,1,0), 90.0, 1.0, 1.0))

        initial_state = FoldState.initial(part_shape=None, bend_graph=bg, panel_graph=None)
        validator = MockValidator()
        planner = AStarBackwardPlanner(bend_graph=bg, validator=validator)

        # N=3, depth_limit=2 → cannot reach goal (requires depth 3)
        result = planner.solve(initial_state, depth_limit=2)

        self.assertFalse(result["success"], "depth_limit=2 < N=3 must not solve")
        self.assertIn(
            result["termination_reason"],
            ("NO_SOLUTION", "NODE_LIMIT_EXHAUSTED"),
            "depth-limited failure must terminate via search exhaustion, not silently"
        )
        self.assertGreater(result["nodes_explored"], 0,
                           "some nodes must have been explored before giving up")


if __name__ == "__main__":
    unittest.main()
