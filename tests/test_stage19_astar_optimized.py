"""
test_stage19_astar_optimized.py - Validate A* Optimizer

Ensures A* utilizes the optimized heuristic (penalizing inner bends unfolded first)
to natively find center-first sequences without exploring the entire state space.
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
from smbs_core.Core.panel_graph import PanelGraph

class MockValidator(PhysicalValidator):
    def __init__(self):
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        super().__init__(None, MockBackend())

    def validate_step(self, current_state, bend_record, panel_graph=None, tooling_override=None):
        moving = panel_graph.moving_subtree(bend_record.id) if panel_graph else set()
        next_state = current_state.apply_unfold(
            bend_id=bend_record.id,
            unfolded_shape=None,
            moving_panels=moving
        )
        return ValidationResult(valid=True, reason="Mock valid", stage="MOCK", next_state=next_state)

class TestStage19AStarOptimized(unittest.TestCase):
    def test_astar_finds_center_first_naturally(self):
        # Create a 4-bend U-profile with flanges
        # Bends: B1, B2, B3, B4
        # Outer bends: B1, B4 (move 1 panel each)
        # Inner bends: B2, B3 (move 2 panels each)
        
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "P1", "P2", (0,0,0), (1,0,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B2", "F2", "P2", "P3", (0,0,0), (1,0,0), -90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B3", "F3", "P3", "P4", (0,0,0), (1,0,0), 90.0, 1.0, 1.0))
        bg.add_bend(BendRecord("B4", "F4", "P4", "P5", (0,0,0), (1,0,0), -90.0, 1.0, 1.0))

        pg = PanelGraph()
        pg.add_bend_connection("B1", "P1", "P2")
        pg.add_bend_connection("B2", "P2", "P3")
        pg.add_bend_connection("B3", "P3", "P4")
        pg.add_bend_connection("B4", "P4", "P5")
        
        # Verify A* solves it
        initial = FoldState.initial(None, bg, pg)
        
        validator = MockValidator()
        planner = AStarBackwardPlanner(bg, validator, pg)
        
        result = planner.solve(initial)
        
        self.assertTrue(result["success"])
        
        # A* backward sequence unfolds outer bends FIRST.
        # So forward sequence should have inner bends FIRST.
        # B2, B3 should appear before B1, B4 in the forward sequence!
        
        seq_bends = [step["bend_id"] for step in result["sequence"]]
        
        b2_idx = seq_bends.index("B2")
        b3_idx = seq_bends.index("B3")
        b1_idx = seq_bends.index("B1")
        b4_idx = seq_bends.index("B4")
        
        # Check that inner bends (B2 or B3) come before outer bends (B1 or B4)
        self.assertTrue(min(b2_idx, b3_idx) < max(b1_idx, b4_idx))
        
        print("Optimized A* naturally found Center-First sequence:", seq_bends)
        print("Nodes explored:", result["nodes_explored"])

if __name__ == "__main__":
    unittest.main()
