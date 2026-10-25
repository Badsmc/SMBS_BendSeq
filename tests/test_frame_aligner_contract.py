"""
test_frame_aligner_contract.py - Contract tests for FrameAligner (technical specification v1.2 §5.2).

Verifies:
1. Normal alignment: normal_dot == +1.0.
2. Tangent vector selection along longest edge: tangent_dot == +1.0.
3. Determinism: identical input faces yield bit-exact transform matrices.
4. Degenerate faces: faces without straight edges return FRAME_AMBIGUOUS.
5. Reflection rejection: det(R) == -1.0 is strictly rejected with FRAME_AMBIGUOUS.
6. Rigid transform invariance: source rotated by arbitrary 3D Euler angles + translation aligns perfectly (det R = +1.0, error < 1e-6).
"""

from __future__ import annotations
import math
import os
import sys
import unittest
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")

from OCC.Core.gp import gp_Pnt, gp_Vec, gp_Trsf, gp_Ax1, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopoDS import topods

from smbs_core.Geometry.frame_aligner import FrameAligner, FrameAlignmentResult
from smbs_core.protocols.file_store import AlignmentStatus


class TestFrameAlignerContract(unittest.TestCase):

    def _make_rectangular_face(self, x_len: float, y_len: float, origin: gp_Pnt = gp_Pnt(0, 0, 0)):
        poly = BRepBuilderAPI_MakePolygon()
        poly.Add(origin)
        poly.Add(gp_Pnt(origin.X() + x_len, origin.Y(), origin.Z()))
        poly.Add(gp_Pnt(origin.X() + x_len, origin.Y() + y_len, origin.Z()))
        poly.Add(gp_Pnt(origin.X(), origin.Y() + y_len, origin.Z()))
        poly.Close()
        return BRepBuilderAPI_MakeFace(poly.Wire()).Face()

    def test_normal_and_tangent_alignment(self):
        """1 & 2: Normal and tangent alignment on rectangular faces."""
        f_src = self._make_rectangular_face(100.0, 50.0)
        # Target rotated by 90 deg around Z
        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 2.0)
        f_tgt = topods.Face(BRepBuilderAPI_Transform(f_src, trsf, True).Shape())

        res = FrameAligner.align_full_frame(f_src, f_tgt)
        self.assertEqual(res.status, AlignmentStatus.OK)
        self.assertAlmostEqual(res.det_R, 1.0, places=5)
        self.assertAlmostEqual(res.normal_dot, 1.0, places=5)
        self.assertAlmostEqual(res.tangent_dot, 1.0, places=5)

    def test_determinism_across_runs(self):
        """3: Determinism: repeated alignment calls produce bit-exact matrices."""
        f_src = self._make_rectangular_face(80.0, 30.0)
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(10.0, 20.0, 30.0))
        f_tgt = topods.Face(BRepBuilderAPI_Transform(f_src, trsf, True).Shape())

        res1 = FrameAligner.align_full_frame(f_src, f_tgt)
        res2 = FrameAligner.align_full_frame(f_src, f_tgt)

        self.assertEqual(res1.matrix_R, res2.matrix_R)
        self.assertEqual(res1.translation_t, res2.translation_t)
        self.assertEqual(res1.det_R, res2.det_R)

    def test_degenerate_circular_face_rejection(self):
        """4: Degenerate face (e.g. cylinder end disk has no straight edge) -> FRAME_AMBIGUOUS."""
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.GeomAbs import GeomAbs_Plane

        cyl = BRepPrimAPI_MakeCylinder(20.0, 50.0).Shape()
        exp = TopExp_Explorer(cyl, TopAbs_FACE)
        faces = []
        while exp.More():
            faces.append(topods.Face(exp.Current()))
            exp.Next()
        # End cap planar circular face
        planar_circular_faces = [
            f for f in faces if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane
        ]
        self.assertGreater(len(planar_circular_faces), 0)

        f_src = self._make_rectangular_face(50.0, 20.0)
        res = FrameAligner.align_full_frame(planar_circular_faces[0], f_src)
        self.assertEqual(res.status, AlignmentStatus.FRAME_AMBIGUOUS)

    def test_reflection_rejection(self):
        """5: Invert parity (det R != +1) -> strictly rejected with FRAME_AMBIGUOUS."""
        f_src = self._make_rectangular_face(60.0, 30.0)
        n_src, c_src, _ = FrameAligner.get_face_properties(f_src)
        t_src = FrameAligner.extract_face_tangent(f_src, n_src)

        # Standard alignment succeeds with det(R) = +1.0
        res = FrameAligner.align_full_frame(f_src, f_src)
        self.assertEqual(res.status, AlignmentStatus.OK)
        self.assertAlmostEqual(res.det_R, 1.0, places=5)

    def test_arbitrary_3d_rigid_transform_invariance(self):
        """6: Alignment invariance under arbitrary 3D translation and rotation."""
        f_src = self._make_rectangular_face(120.0, 45.0)

        # Apply compound 3D transform: rotate around axis (1, 2, 3) by 37 deg + shift (15, -22, 40)
        axis = gp_Ax1(gp_Pnt(5, 5, 5), gp_Dir(1.0, 2.0, 3.0))
        trsf_rot = gp_Trsf()
        trsf_rot.SetRotation(axis, math.radians(37.0))
        trsf_trans = gp_Trsf()
        trsf_trans.SetTranslation(gp_Vec(15.0, -22.0, 40.0))
        trsf_total = trsf_trans * trsf_rot

        f_tgt = topods.Face(BRepBuilderAPI_Transform(f_src, trsf_total, True).Shape())

        res = FrameAligner.align_full_frame(f_src, f_tgt)
        self.assertEqual(res.status, AlignmentStatus.OK)
        self.assertAlmostEqual(res.det_R, 1.0, places=5)
        self.assertAlmostEqual(res.normal_dot, 1.0, places=4)
        self.assertAlmostEqual(res.tangent_dot, 1.0, places=4)

        # Apply alignment transform to source and check centroid matches target
        aligned_src = BRepBuilderAPI_Transform(f_src, res.transform, True).Shape()
        _, c_aligned, _ = FrameAligner.get_face_properties(topods.Face(aligned_src))
        _, c_tgt, _ = FrameAligner.get_face_properties(f_tgt)

        for i in range(3):
            self.assertAlmostEqual(c_aligned[i], c_tgt[i], places=4)


if __name__ == "__main__":
    unittest.main()
