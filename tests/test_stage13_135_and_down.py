"""
test_stage13_135_and_down.py - Stage 13 Validation (135 degree and DOWN bends)

Verifies:
- signed-angle model correctly processes acute angles (135 and -135).
- DOWN bends (typically negative angles) are processed correctly for unfolding.
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

class TestStage13AnglesAndDown(unittest.TestCase):
    
    def test_135_degree_bends(self):
        # Bend +135
        b_pos135 = BendRecord("B_135_POS", "F1", "P1", "P2", 
                              axis_origin=(0.0, 0.0, 0.0), 
                              axis_direction=(0.0, 1.0, 0.0), 
                              signed_angle=135.0, 
                              radius=1.0, thickness=1.0)
                              
        # Bend -135
        b_neg135 = BendRecord("B_135_NEG", "F2", "P2", "P3", 
                              axis_origin=(10.0, 0.0, 0.0), 
                              axis_direction=(0.0, 1.0, 0.0), 
                              signed_angle=-135.0, 
                              radius=1.0, thickness=1.0, bend_sign=-1)
                              
        self.assertEqual(b_pos135.bend_sign, 1)
        self.assertEqual(b_neg135.bend_sign, -1)
        
        # Test unfold of +135 (rotate by -135)
        # Point at (10,0,0) around (0,0,0)
        rot_pos135 = BendTransform.transform_point(
            point=(10.0, 0.0, 0.0),
            axis_origin=b_pos135.axis_origin,
            axis_direction=b_pos135.axis_direction,
            signed_angle_deg=-b_pos135.signed_angle
        )
        self.assertAlmostEqual(rot_pos135[0], 10.0 * math.cos(math.radians(-135.0)), places=5)
        self.assertAlmostEqual(rot_pos135[1], 0.0, places=5)
        self.assertAlmostEqual(rot_pos135[2], -10.0 * math.sin(math.radians(-135.0)), places=5)
        
        # Test unfold of -135 (rotate by +135)
        # Point at (20,0,0) around (10,0,0)
        rot_neg135 = BendTransform.transform_point(
            point=(20.0, 0.0, 0.0),
            axis_origin=b_neg135.axis_origin,
            axis_direction=b_neg135.axis_direction,
            signed_angle_deg=-b_neg135.signed_angle
        )
        self.assertAlmostEqual(rot_neg135[0], 10.0 + 10.0 * math.cos(math.radians(135.0)), places=5)
        self.assertAlmostEqual(rot_neg135[1], 0.0, places=5)
        self.assertAlmostEqual(rot_neg135[2], -10.0 * math.sin(math.radians(135.0)), places=5)

    def test_down_bends(self):
        # DOWN bends are simply bends with a negative sign (e.g. -90, -45) depending on axis definition.
        # DOWN -90
        b_down90 = BendRecord("B_DOWN_90", "F3", "P3", "P4", 
                              axis_origin=(0.0, 0.0, 0.0), 
                              axis_direction=(0.0, 1.0, 0.0), 
                              signed_angle=-90.0, 
                              radius=1.0, thickness=1.0, bend_sign=-1)
                              
        # DOWN -45
        b_down45 = BendRecord("B_DOWN_45", "F4", "P4", "P5", 
                              axis_origin=(10.0, 0.0, 0.0), 
                              axis_direction=(0.0, 1.0, 0.0), 
                              signed_angle=-45.0, 
                              radius=1.0, thickness=1.0, bend_sign=-1)
                              
        self.assertEqual(b_down90.bend_sign, -1)
        self.assertEqual(b_down45.bend_sign, -1)
        
        # Unfold DOWN -90 (rotate by +90)
        rot_down90 = BendTransform.transform_point(
            point=(10.0, 0.0, 0.0),
            axis_origin=b_down90.axis_origin,
            axis_direction=b_down90.axis_direction,
            signed_angle_deg=-b_down90.signed_angle
        )
        self.assertAlmostEqual(rot_down90[0], 10.0 * math.cos(math.radians(90.0)), places=5)
        self.assertAlmostEqual(rot_down90[1], 0.0, places=5)
        self.assertAlmostEqual(rot_down90[2], -10.0 * math.sin(math.radians(90.0)), places=5)
        
        # Unfold DOWN -45 (rotate by +45)
        rot_down45 = BendTransform.transform_point(
            point=(20.0, 0.0, 0.0),
            axis_origin=b_down45.axis_origin,
            axis_direction=b_down45.axis_direction,
            signed_angle_deg=-b_down45.signed_angle
        )
        self.assertAlmostEqual(rot_down45[0], 10.0 + 10.0 * math.cos(math.radians(45.0)), places=5)
        self.assertAlmostEqual(rot_down45[1], 0.0, places=5)
        self.assertAlmostEqual(rot_down45[2], -10.0 * math.sin(math.radians(45.0)), places=5)

if __name__ == "__main__":
    unittest.main()
