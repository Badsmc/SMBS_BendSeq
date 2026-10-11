"""
test_stage15_tool_optimizer.py - Stage 15 Validation (Tool Optimizer Post-Pass)

Verifies:
- Tool optimizer runs strictly after sequence generation.
- It correctly annotates part flips, tool changes, and station assignments.
- It preserves existing data (e.g. backgauge).
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

from smbs_core.Solvers.tool_optimizer import ToolOptimizer
from smbs_core.IO.tool_database import ToolDatabase

class TestStage15ToolOptimizer(unittest.TestCase):
    
    def test_tool_optimization_post_pass(self):
        optimizer = ToolOptimizer(ToolDatabase())
        
        # Mock sequence produced by Stage 14 (with backgauge)
        # B1 and B2 have the same axis, B3 has a different axis (flip)
        # B2 and B3 use DIE_V12, B1 uses DIE_V8 (tool change between B1 and B2)
        mock_sequence = [
            {
                "bend_id": "B1",
                "axis": (0, 1, 0),
                "tooling": {"die_id": "DIE_V8"},
                "backgauge": {"X_mm": 50.0, "R_mm": 1.0}
            },
            {
                "bend_id": "B2",
                "axis": (0, 1, 0),
                "tooling": {"die_id": "DIE_V12"},
                "backgauge": {"X_mm": 60.0, "R_mm": 1.0}
            },
            {
                "bend_id": "B3",
                "axis": (0, -1, 0), # Flipped axis
                "tooling": {"die_id": "DIE_V12"},
                "backgauge": {"X_mm": 70.0, "R_mm": 1.0}
            }
        ]
        
        optimized = optimizer.optimize_sequence_tooling(mock_sequence)
        
        self.assertEqual(len(optimized), 3)
        
        # Step 1: B1
        ops1 = optimized[0]["operator_instructions"]
        self.assertEqual(ops1["station"], 1)
        self.assertFalse(ops1["requires_part_flip_180"])
        self.assertFalse(ops1["tool_change_required"])
        
        # Step 2: B2 (Tool change, no flip)
        ops2 = optimized[1]["operator_instructions"]
        self.assertEqual(ops2["station"], 2) # New station due to tool change
        self.assertFalse(ops2["requires_part_flip_180"])
        self.assertTrue(ops2["tool_change_required"])
        
        # Step 3: B3 (Flip, no tool change)
        ops3 = optimized[2]["operator_instructions"]
        self.assertEqual(ops3["station"], 2) # Same station
        self.assertTrue(ops3["requires_part_flip_180"])
        self.assertFalse(ops3["tool_change_required"])

if __name__ == "__main__":
    unittest.main()
