"""
test_sheetmetal_ubracket.py - Unit test suite for 2-bend U-bracket multi-bend unfolding
"""

import os
import sys
import unittest
import math

sys.path.insert(0, '/Users/badmc/Desktop/SMBS')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.api.sheetmetal_client import SheetMetalApiClient
import FreeCAD
import Part
import tempfile
from OCC.Core.gp import gp_Pnt, gp_Vec
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs


def create_u_bracket_shape_perfect_concentric(l1: float = 95.5, l_mid: float = 50.0, l2: float = 47.5, radius: float = 1.0, thickness: float = 1.5, length: float = 50.0):
    """
    Creates a 2-bend U-bracket with 100% concentric inner & outer fillets for both bends.
    Center 1 = (2.5, 2.5), Center 2 = (52.5, 2.5), L_mid_flat = 50.0mm.
    l_mid = flat length of middle flange between bend tangent points, not outer bounding measurement.
    """
    r_in = radius
    r_out = radius + thickness
    t = thickness

    c1_x = r_in + t # 2.5
    c1_z = t + r_in # 2.5

    c2_x = c1_x + l_mid # 52.5
    c2_z = t + r_in # 2.5

    poly = BRepBuilderAPI_MakePolygon()
    poly.Add(gp_Pnt(0.0, 0.0, c1_z + l1))             # Top leg 1 outer
    poly.Add(gp_Pnt(t, 0.0, c1_z + l1))               # Top leg 1 inner
    poly.Add(gp_Pnt(t, 0.0, t))                        # Inner corner 1 (X=1.5, Z=1.5)
    poly.Add(gp_Pnt(c2_x + r_in, 0.0, t))              # Inner corner 2 (X=53.5, Z=1.5)
    poly.Add(gp_Pnt(c2_x + r_in, 0.0, c2_z + l2))      # Top leg 2 inner (X=53.5)
    poly.Add(gp_Pnt(c2_x + r_out, 0.0, c2_z + l2))     # Top leg 2 outer (X=55.0)
    poly.Add(gp_Pnt(c2_x + r_out, 0.0, 0.0))           # Outer corner 2 (X=55.0, Z=0.0)
    poly.Add(gp_Pnt(0.0, 0.0, 0.0))                    # Outer corner 1 (X=0.0, Z=0.0)
    poly.Close()

    wire = poly.Wire()
    face = BRepBuilderAPI_MakeFace(wire).Shape()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, length, 0.0)).Shape()

    fillet = BRepFilletAPI_MakeFillet(prism)
    exp = TopExp_Explorer(prism, TopAbs_EDGE)
    while exp.More():
        e = exp.Current()
        c = BRepAdaptor_Curve(e)
        p1 = c.Value(c.FirstParameter())
        p2 = c.Value(c.LastParameter())
        if abs(p1.X() - p2.X()) < 1e-3 and abs(p1.Z() - p2.Z()) < 1e-3 and abs(p1.Y() - p2.Y()) > 10.0:
            if abs(p1.X() - t) < 1e-2 and abs(p1.Z() - t) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(p1.X() - (c2_x + r_in)) < 1e-2 and abs(p1.Z() - t) < 1e-2:
                fillet.Add(r_in, e)
            elif abs(p1.X() - 0.0) < 1e-2 and abs(p1.Z() - 0.0) < 1e-2:
                fillet.Add(r_out, e)
            elif abs(p1.X() - (c2_x + r_out)) < 1e-2 and abs(p1.Z() - 0.0) < 1e-2:
                fillet.Add(r_out, e)
        exp.Next()
    fillet.Build()
    return fillet.Shape()


class TestSheetMetalUBracket(unittest.TestCase):

    def setUp(self):
        import FreeCAD
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        self._saved_k = params.GetFloat("manualKFactor", 0.44)
        self._saved_std = params.GetString("kFactorStandard", "ansi")
        params.SetFloat("manualKFactor", 0.44)
        params.SetString("kFactorStandard", "ansi")
        self.client = SheetMetalApiClient()

    def tearDown(self):
        import FreeCAD
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        params.SetFloat("manualKFactor", self._saved_k)
        params.SetString("kFactorStandard", self._saved_std)


    def test_u_bracket_multibend_unfold(self):
        u_occ = create_u_bracket_shape_perfect_concentric(l1=95.5, l_mid=50.0, l2=47.5, radius=1.0, thickness=1.5)

        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(u_occ, STEPControl_AsIs)
        writer.Write(step_path)
        s0_fc = Part.read(step_path)
        os.remove(step_path)

        # Raw input shape face metrics
        faces_in = s0_fc.Faces
        cyls_in = [f for f in faces_in if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertEqual(len(cyls_in), 4)

        # Unfold 2-bend shape
        unfolded = self.client.unfold_shape_headless(s0_fc)
        self.assertIsNotNone(unfolded)

        faces_out = unfolded.Faces
        cyls_out = [f for f in faces_out if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']

        self.assertEqual(len(cyls_out), 0)
        self.assertEqual(len(faces_out), 14) # Full 14-face B-Rep solid
        self.assertNotEqual(len(faces_out), 6) # Not a synthetic 6-face box

        bb = unfolded.BoundBox
        w_flat = max(bb.XLength, bb.YLength, bb.ZLength)

        # Theoretical neutral axis flat length:
        # Convention: L_mid = 50.0 is the exact flat length between inner bend cylinder centers (C1=(2.5, 2.5), C2=(52.5, 2.5)).
        # BA = pi * (R + K * t) * 90 / 180 = pi * (1.0 + 0.44 * 1.5) * 90 / 180 = 2.6075219 mm
        # W_flat = L1 + BA + L_mid + BA + L2 = 95.5 + 2.6075219 + 50.0 + 2.6075219 + 47.5 = 198.2150438 mm
        ba_formula = (math.pi * (1.0 + 0.44 * 1.5) * 90.0) / 180.0
        expected_w_flat = 95.5 + ba_formula + 50.0 + ba_formula + 47.5

        # Verify solid dimensions:
        self.assertAlmostEqual(w_flat, expected_w_flat, delta=1e-3)
        self.assertAlmostEqual(bb.ZLength, expected_w_flat, delta=1e-3)
        self.assertAlmostEqual(bb.XLength, 1.5, delta=1e-3) # Sheet thickness
        self.assertAlmostEqual(bb.YLength, 50.0, delta=1e-3) # Sheet width

        # Verify area of primary flat faces: Area = length * width = 198.215044 * 50.0 = 9910.7522 mm2
        large_faces = [f for f in faces_out if f.Area > 5000.0]
        self.assertEqual(len(large_faces), 2)
        for f in large_faces:
            self.assertAlmostEqual(f.Area, expected_w_flat * 50.0, delta=1e-2)


if __name__ == '__main__':
    unittest.main()

