"""
test_phase2_kinematics.py - Phase 2 Kinematic Backend Test Suite

Verifies LAW-2 Single Kinematic Backend requirements:
- Rodrigues rotation matrix calculation:
    R(θ) = I*cos(θ) + (1-cos(θ))*u*u^T + [u]_x*sin(θ)
- 3D point transformation around arbitrary axis line (C, u).
- TopoShape subtree rotation through BendTransform.rotate_subtree.
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

from smbs_core.Core.bend_transform import BendTransform

try:
    import FreeCAD
    import Part
    HAS_FREECAD = True
except ImportError:
    HAS_FREECAD = False


class TestPhase2Kinematics(unittest.TestCase):
    """Phase 2 Kinematic Backend & Rodrigues Rotation Tests."""

    def test_rodrigues_identity_at_zero(self):
        """Test that Rodrigues matrix at 0 degrees is the Identity matrix."""
        R = BendTransform.rodrigues_rotation_matrix((0.0, 0.0, 1.0), 0.0)
        for i in range(3):
            for j in range(3):
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(R[i][j], expected, delta=1e-6)

    def test_rodrigues_90deg_z_axis(self):
        """Test Rodrigues matrix for 90 degree rotation around Z axis."""
        # 90 deg around (0,0,1): (1,0,0) -> (0,1,0)
        P = (10.0, 0.0, 0.0)
        origin = (0.0, 0.0, 0.0)
        axis = (0.0, 0.0, 1.0)
        
        P_prime = BendTransform.transform_point(P, origin, axis, 90.0)
        self.assertAlmostEqual(P_prime[0], 0.0, delta=1e-4)
        self.assertAlmostEqual(P_prime[1], 10.0, delta=1e-4)
        self.assertAlmostEqual(P_prime[2], 0.0, delta=1e-4)

    def test_transform_point_offset_origin(self):
        """Test point rotation around offset axis origin C=(5, 5, 0)."""
        P = (15.0, 5.0, 0.0)
        origin = (5.0, 5.0, 0.0)
        axis = (0.0, 0.0, 1.0)
        
        # Point is offset by +10 in X from origin. Rotating 90 deg -> offset +10 in Y
        P_prime = BendTransform.transform_point(P, origin, axis, 90.0)
        self.assertAlmostEqual(P_prime[0], 5.0, delta=1e-4)
        self.assertAlmostEqual(P_prime[1], 15.0, delta=1e-4)
        self.assertAlmostEqual(P_prime[2], 0.0, delta=1e-4)

    def test_rotate_subtree_toposhape(self):
        """Test CAD TopoShape subtree rotation."""
        if not HAS_FREECAD:
            self.skipTest("FreeCAD not available in environment.")

        box = Part.makeBox(10, 10, 10)
        origin = (0.0, 0.0, 0.0)
        axis = (0.0, 0.0, 1.0)
        
        rotated_box = BendTransform.rotate_subtree(box, origin, axis, 90.0)
        self.assertIsNotNone(rotated_box)
        # Check center of mass shift
        cm_orig = box.CenterOfMass
        cm_rot = rotated_box.CenterOfMass
        self.assertAlmostEqual(cm_rot.x, -cm_orig.y, delta=1e-3)
        self.assertAlmostEqual(cm_rot.y, cm_orig.x, delta=1e-3)


if __name__ == "__main__":
    unittest.main()
