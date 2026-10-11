"""
test_stage10_candidate_ordering.py - Stage 10 Candidate Ordering Validation

Verifies:
- DFS backward solver sorts candidates using score_candidate.
- Candidates with smaller moving subtrees, no flips, and longer lengths are tried first.
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

from smbs_core.Solvers.dfs_backward import DFSBackwardPlanner
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph

class MockValidator:
    def validate_step(self, *args, **kwargs):
        class Res:
            valid = True
            reason = "OK"
            stage = "valid"
            next_state = FoldState(shape=None, folded_bends=set(), remaining_bends=set())
            diagnostics = {"signed_angle": 90.0}
        return Res()

class TestStage10Ordering(unittest.TestCase):
    def test_candidate_ordering(self):
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 1.0, 1.0, length=100.0))
        bg.add_bend(BendRecord("B2", "F2", "PB", "PC", (0,0,0), (1,0,0), -90.0, 1.0, 1.0, length=50.0))
        bg.add_bend(BendRecord("B3", "F3", "PC", "PD", (0,0,0), (1,0,0), 90.0, 1.0, 1.0, length=200.0))

        pg = PanelGraph()
        pg.add_bend_connection("B1", "PA", "PB")
        pg.add_bend_connection("B2", "PB", "PC")
        pg.add_bend_connection("B3", "PC", "PD")
        
        # B1 moving subtree = PB, PC, PD (size 3) => score += 30.0
        # B2 moving subtree = PC, PD (size 2) => score += 20.0
        # B3 moving subtree = PD (size 1) => score += 10.0
        
        # Lengths: B1=100 (-1.0), B2=50 (-0.5), B3=200 (-2.0)
        # Expected scores (no previous bends):
        # B1: 30.0 - 1.0 = 29.0
        # B2: 20.0 - 0.5 = 19.5
        # B3: 10.0 - 2.0 = 8.0
        
        planner = DFSBackwardPlanner(bg, MockValidator(), pg)
        
        initial_state = FoldState(shape=None, folded_bends=set(), remaining_bends={"B1", "B2", "B3"})
        
        # We can extract the inner score_candidate logic by running solve and printing or just inspecting the test logic
        # Actually, let's just monkey-patch sorted to capture the order
        original_sorted = sorted
        captured_order = []
        
        def mock_sorted(iterable, key=None, reverse=False):
            res = original_sorted(iterable, key=key, reverse=reverse)
            if hasattr(key, '__name__') and key.__name__ == 'score_candidate':
                captured_order.append(res)
            return res
            
        import builtins
        builtins.sorted = mock_sorted
        
        try:
            planner.solve(initial_state)
            
            # The first sort call should order them as B3, B2, B1
            self.assertTrue(len(captured_order) > 0)
            first_order = captured_order[0]
            self.assertEqual(first_order, ["B3", "B2", "B1"])
            
        finally:
            builtins.sorted = original_sorted

if __name__ == "__main__":
    unittest.main()
