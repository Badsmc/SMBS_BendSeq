"""
test_stage12_opposite_bends.py - Stage 12 Validation (45 / -45 opposite bends)

Verifies:
- Bend axis and bend sign are respected.
- Moving subtree pivots exactly by the specified angle magnitude and direction.
- Final normals (if mocked by vectors) rotate correctly.
"""

import sys
import os
import unittest
import math

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from smbs_core.Core.bend_graph import BendRecord
from smbs_core.Core.bend_transform import BendTransform

class TestStage12OppositeBends(unittest.TestCase):
    def test_opposite_45_degree_bends(self):
        # Bend 1: +45 degrees
        b1 = BendRecord("B1", "F1", "PA", "PB", 
                        axis_origin=(0.0, 0.0, 0.0), 
                        axis_direction=(0.0, 1.0, 0.0), # Y-axis
                        signed_angle=45.0, 
                        radius=1.0, 
                        thickness=1.0)
                        
        # Bend 2: -45 degrees
        b2 = BendRecord("B2", "F2", "PB", "PC", 
                        axis_origin=(100.0, 0.0, 0.0), 
                        axis_direction=(0.0, 1.0, 0.0), 
                        signed_angle=-45.0, 
                        radius=1.0, 
                        thickness=1.0,
                        bend_sign=-1)
                        
        # Validate bend sign logic
        self.assertEqual(b1.bend_sign, 1)
        self.assertEqual(b2.bend_sign, -1)
        
        # Test rotate_point for +45 degree unfold (unfolding means we rotate by -45 degrees)
        # Point at (10, 0, 0) rotated by -45 deg around Y axis (0, 1, 0)
        # Expected: X = 10*cos(-45), Z = -10*sin(-45)
        # Note: unfolding B1 applies -45.0 degrees.
        rotated_b1 = BendTransform.transform_point(
            point=(10.0, 0.0, 0.0),
            axis_origin=b1.axis_origin,
            axis_direction=b1.axis_direction,
            signed_angle_deg=-b1.signed_angle
        )
        
        self.assertAlmostEqual(rotated_b1[0], 10.0 * math.cos(math.radians(-45.0)), places=5)
        self.assertAlmostEqual(rotated_b1[1], 0.0, places=5)
        self.assertAlmostEqual(rotated_b1[2], -10.0 * math.sin(math.radians(-45.0)), places=5)
        
        # Test rotate_point for -45 degree unfold (unfolding means we rotate by +45 degrees)
        # Point at (110, 0, 0) rotated around origin (100, 0, 0) by +45 deg around Y axis
        # Expected relative X = 10*cos(45), relative Z = -10*sin(45)
        # Expected absolute X = 100 + 10*cos(45), Z = -10*sin(45)
        rotated_b2 = BendTransform.transform_point(
            point=(110.0, 0.0, 0.0),
            axis_origin=b2.axis_origin,
            axis_direction=b2.axis_direction,
            signed_angle_deg=-b2.signed_angle
        )
        
        self.assertAlmostEqual(rotated_b2[0], 100.0 + 10.0 * math.cos(math.radians(45.0)), places=5)
        self.assertAlmostEqual(rotated_b2[1], 0.0, places=5)
        self.assertAlmostEqual(rotated_b2[2], -10.0 * math.sin(math.radians(45.0)), places=5)

if __name__ == "__main__":
    unittest.main()
