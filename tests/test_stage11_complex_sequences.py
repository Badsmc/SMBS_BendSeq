"""
test_stage11_complex_sequences.py - Stage 11 Validation (L/U/C & Center-first topologies)

Verifies:
- DFS Solver correctly traverses L, U, C profiles.
- Validates that Center-first sequence (2->1->3->4) is reachable if Oracle approves.
"""

import sys
import os
import unittest
from typing import Dict, Any, List

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
    def __init__(self, invalid_bends=None):
        # invalid_bends is a dict: state_fingerprint -> set of bend_ids that fail
        self.invalid_bends = invalid_bends or {}

    def validate_step(self, current: FoldState, bend_rec: BendRecord, panel_graph: Any, tooling_override=None):
        class Res:
            pass
        res = Res()
        
        # Determine if this step is mock-invalid
        folded_tuple = tuple(sorted(current.folded_bends))
        print(f"Validating {bend_rec.id} in {folded_tuple}. Invalid: {self.invalid_bends.get(folded_tuple, set())}")
        if bend_rec.id in self.invalid_bends.get(folded_tuple, set()):
            res.valid = False
            res.reason = "Mock collision"
            res.stage = "TOOL"
            return res
            
        res.valid = True
        res.reason = "OK"
        res.stage = "valid"
        res.next_state = current.apply_unfold(bend_rec.id, None, set())
        res.diagnostics = {"signed_angle": bend_rec.signed_angle}
        return res

class TestStage11ComplexSequences(unittest.TestCase):
    def test_l_profile(self):
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 1.0, 1.0, length=100.0))
        bg.add_bend(BendRecord("B2", "F2", "PB", "PC", (0,0,0), (1,0,0), -90.0, 1.0, 1.0, length=100.0))

        pg = PanelGraph()
        pg.add_bend_connection("B1", "PA", "PB")
        pg.add_bend_connection("B2", "PB", "PC")

        planner = DFSBackwardPlanner(bg, MockValidator(), pg)
        initial_state = FoldState.initial(None, bg, pg)
        
        path = planner.solve(initial_state)
        self.assertIsNotNone(path)
        sequence = path.get("sequence", [])
        self.assertEqual(len(sequence), 2)

    def test_center_first_sequence(self):
        """
        Tests a 4-bend profile where we want to verify that the sequence 
        Forward: B2 -> B1 -> B3 -> B4 
        (Backward: B4 -> B3 -> B1 -> B2)
        can be found by the DFS if other paths are blocked (simulate physical constraints).
        """
        bg = BendGraph()
        bg.add_bend(BendRecord("B1", "F1", "P1", "P2", (0,0,0), (1,0,0), 90.0, 1.0, 1.0, length=100.0))
        bg.add_bend(BendRecord("B2", "F2", "P2", "P3", (0,0,0), (1,0,0), -90.0, 1.0, 1.0, length=100.0))
        bg.add_bend(BendRecord("B3", "F3", "P3", "P4", (0,0,0), (1,0,0), 90.0, 1.0, 1.0, length=100.0))
        bg.add_bend(BendRecord("B4", "F4", "P4", "P5", (0,0,0), (1,0,0), -90.0, 1.0, 1.0, length=100.0))

        pg = PanelGraph()
        pg.add_bend_connection("B1", "P1", "P2")
        pg.add_bend_connection("B2", "P2", "P3")
        pg.add_bend_connection("B3", "P3", "P4")
        pg.add_bend_connection("B4", "P4", "P5")
        
        # We want to force the solver to output the path:
        # B4, B3, B1, B2 (backward order) => Forward: B2, B1, B3, B4
        
        # Let's block other branches.
        # Initial state (all un-unfolded): B1, B2, B3, B4. Only B4 is valid to unfold.
        initial = FoldState.initial(None, bg, pg)
        
        # Once B4 is unfolded, state is B1, B2, B3. Block B2, let B3 be valid.
        state_after_B4 = initial.apply_unfold("B4", None, set())
        
        # Once B3 is unfolded, state is B1, B2. Block B2, let B1 be valid.
        state_after_B3 = state_after_B4.apply_unfold("B3", None, set())
        
        invalid_map = {
            tuple(sorted(["B1", "B2", "B3", "B4"])): {"B1", "B2", "B3"},
            tuple(sorted(["B1", "B2", "B3"])): {"B1", "B2"},
            tuple(sorted(["B1", "B2"])): {"B2"}
        }
        
        validator = MockValidator(invalid_bends=invalid_map)
        planner = DFSBackwardPlanner(bg, validator, pg)
        
        path = planner.solve(initial)
        self.assertIsNotNone(path)
        sequence = path.get("sequence", [])
        self.assertEqual(len(sequence), 4)
        
        # The DFS backward solver already returns the forward folding sequence in path["sequence"]
        forward_sequence = [step["bend_id"] for step in sequence]
        
        self.assertEqual(forward_sequence, ["B2", "B1", "B3", "B4"])

if __name__ == "__main__":
    unittest.main()
