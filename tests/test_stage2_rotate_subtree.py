"""
test_stage2_rotate_subtree.py - Stage 2 Kinematics & Subtree Transformation Test Suite

Verifies PanelGraph moving_subtree calculation and true rigid-body subtree rotation:
For panel chain A - B - C - D with bend at (B|C):
- Moving subtree is {C, D}
- Fixed subtree is {A, B}
- Rotating bend (B|C) transforms ONLY moving subtree panels {C, D}.
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

from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.Core.bend_transform import BendTransform


class TestStage2SubtreeRotation(unittest.TestCase):
    """Stage 2 True Subtree Kinematics Tests."""

    def test_moving_subtree_resolution(self):
        """Test PanelGraph subtree partitioning for chain A - B - C - D."""
        pg = PanelGraph()
        pg.add_bend_connection("BEND_AB", "PA", "PB")
        pg.add_bend_connection("BEND_BC", "PB", "PC")
        pg.add_bend_connection("BEND_CD", "PC", "PD")

        # Folding at bend B|C (BEND_BC)
        moving = pg.moving_subtree("BEND_BC")
        fixed = pg.fixed_set("BEND_BC")

        self.assertEqual(moving, {"PC", "PD"})
        self.assertEqual(fixed, {"PA", "PB"})

    def test_rodrigues_point_transformation(self):
        """Test point rotation around specified axis using Rodrigues formula."""
        origin = (0.0, 0.0, 0.0)
        axis = (0.0, 0.0, 1.0) # Z axis
        point = (10.0, 0.0, 0.0)
        
        # Rotate 90 deg around Z
        transformed = BendTransform.transform_point(point, origin, axis, 90.0)
        self.assertAlmostEqual(transformed[0], 0.0, places=4)
        self.assertAlmostEqual(transformed[1], 10.0, places=4)
        self.assertAlmostEqual(transformed[2], 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
