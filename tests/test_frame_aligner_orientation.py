"""
Unit tests for FrameAligner orientation-aware normal calculation (P0-GEOM / H5).
"""

import unittest
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.gp import gp_Pln, gp_Pnt, gp_Dir
from OCC.Core.TopAbs import TopAbs_FORWARD, TopAbs_REVERSED

from smbs_core.Geometry.frame_aligner import FrameAligner


class TestFrameAlignerOrientation(unittest.TestCase):
    def setUp(self):
        pln = gp_Pln(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
        self.face = BRepBuilderAPI_MakeFace(pln, -10.0, 10.0, -10.0, 10.0).Face()

    def test_get_face_properties_forward_orientation(self):
        """FORWARD face preserves surface plane normal (+Z)."""
        self.face.Orientation(TopAbs_FORWARD)
        normal, centroid, area = FrameAligner.get_face_properties(self.face)
        self.assertAlmostEqual(normal[0], 0.0, places=6)
        self.assertAlmostEqual(normal[1], 0.0, places=6)
        self.assertAlmostEqual(normal[2], 1.0, places=6)
        self.assertAlmostEqual(area, 400.0, places=3)

    def test_get_face_properties_reversed_orientation(self):
        """REVERSED face inverts outward normal to -Z."""
        self.face.Orientation(TopAbs_REVERSED)
        normal, centroid, area = FrameAligner.get_face_properties(self.face)
        self.assertAlmostEqual(normal[0], 0.0, places=6)
        self.assertAlmostEqual(normal[1], 0.0, places=6)
        self.assertAlmostEqual(normal[2], -1.0, places=6)
        self.assertAlmostEqual(area, 400.0, places=3)


if __name__ == "__main__":
    unittest.main()
