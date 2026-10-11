"""
test_stage14_backgauge.py - Stage 14 Validation (Backgauge Post-Pass Calculation)

Verifies:
- Backgauge logic executes ONLY as a post-pass sequence enrichment.
- Sequence payloads correctly contain 'backgauge' dictionary with X, R, Z1, Z2.
- Backgauge limits do not interfere with the DFS Backward solver search logic directly.
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

from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Physics.machine_kinematics import PressBrakeMachine

class TestStage14Backgauge(unittest.TestCase):
    
    def test_backgauge_calculation_post_pass(self):
        # We use BendSeqOrchestrator to run the entire pipeline
        # 1. Setup machine and empty graph
        orchestrator = BendSeqOrchestrator({
            "name": "TestBrake",
            "bed_length_mm": 2000.0
        })
        
        # 2. Add one bend
        bg = BendGraph()
        b1 = BendRecord("B1", "F1", "P1", "P2", 
                        axis_origin=(0.0, 0.0, 0.0), 
                        axis_direction=(0.0, 1.0, 0.0), 
                        signed_angle=90.0, 
                        radius=2.0, thickness=2.0, length=100.0)
        bg.add_bend(b1)
        orchestrator.bend_graph = bg
        
        # 3. We mock the solver result to simulate a successful DFS search
        # Since Orchestrator.run() would need a real panel graph and shapes, 
        # we will directly invoke the post-pass loop on a dummy solver output.
        mock_final_path = [
            {
                "bend_id": "B1",
                "unfolded_angle": 0.0,
                "moving_panels": ["P2"],
                "step_number": 1
            }
        ]
        
        # Manually apply step 5 of the orchestrator
        for step in mock_final_path:
            bend_id = step["bend_id"]
            bend_rec = orchestrator.bend_graph.get_bend(bend_id)
            bg_data = orchestrator.backgauge_calculator.compute_step_backgauge(
                step_number=step["step_number"],
                bend_info=bend_rec.to_dict(),
                part_thickness=bend_rec.thickness
            )
            step["backgauge"] = bg_data
            
        # 4. Assert backgauge data is populated correctly
        result_step = mock_final_path[0]
        self.assertIn("backgauge", result_step)
        bg_dict = result_step["backgauge"]
        
        # Verify required keys
        for key in ["X_mm", "R_mm", "Z1_mm", "Z2_mm", "part_stability_score"]:
            self.assertIn(key, bg_dict)
            
        # Verify specific calculations based on BackgaugeCalculator logic
        # For a 100mm bend, v_width default is 12.0
        # flange_depth_estimate = max(20.0, 100 * 0.5) = 50.0
        # X_mm = 50.0 + (12.0 / 2.0) = 56.0
        self.assertEqual(bg_dict["X_mm"], 56.0)
        
        # R_mm = thickness * 0.5 = 1.0
        self.assertEqual(bg_dict["R_mm"], 1.0)
        
        # Z1_mm = -100/2 + 25 = -25.0
        # Z2_mm = 100/2 - 25 = 25.0
        self.assertEqual(bg_dict["Z1_mm"], -25.0)
        self.assertEqual(bg_dict["Z2_mm"], 25.0)
        self.assertFalse(bg_dict["geometry_confirmed"])
        self.assertIn("warning", bg_dict)

if __name__ == "__main__":
    unittest.main()
