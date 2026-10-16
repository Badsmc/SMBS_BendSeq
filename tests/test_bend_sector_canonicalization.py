import unittest
import math
import FreeCAD, Part
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
import SheetMetalBendSolid

class TestBendSectorCanonicalization(unittest.TestCase):
    """
    Unit test for SheetMetalFolder._canonicalize_bend_sector.
    Verifies that for an individual bend sector:
    1. All faces become GeomAbs_Plane or GeomAbs_Cylinder (no GeomAbs_BSplineSurface).
    2. Volume is preserved within strict geometric tolerance (< 1e-4 mm^3).
    3. The resulting shape is topological valid (BRepCheck_Analyzer.IsValid() == True).
    """

    def test_canonicalize_single_bend_sector(self):
        bend_r = 1.0
        thickness = 1.5
        kfactor = 0.44
        angle_deg = 45.0
        width = 50.0

        neutral_r = bend_r + kfactor * thickness
        unfold_len = neutral_r * math.radians(angle_deg)

        p1 = FreeCAD.Vector(0, 0, thickness)
        p2 = FreeCAD.Vector(unfold_len, 0, thickness)
        p3 = FreeCAD.Vector(unfold_len, width, thickness)
        p4 = FreeCAD.Vector(0, width, thickness)

        bend_face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
        bend_edge = Part.makeLine(p1, p4)
        axis = FreeCAD.Vector(0, 1.0, 0)

        # Generate raw bend solid using BRepOffsetAPI_MakeOffsetShape
        s_raw = SheetMetalBendSolid.bend_solid(
            sel_face=bend_face,
            sel_edge=bend_edge,
            bend_r=bend_r,
            thickness=thickness,
            neutral_radius=neutral_r,
            axis=axis,
            flipped=False
        )

        # Verify raw solid contains BSpline faces on the side caps
        has_non_analytical_raw = False
        exp_raw = TopExp_Explorer(SheetMetalFolder._to_occ_shape(s_raw), TopAbs_FACE)
        while exp_raw.More():
            st = BRepAdaptor_Surface(exp_raw.Current(), True).GetType()
            if st not in (GeomAbs_Plane, GeomAbs_Cylinder):
                has_non_analytical_raw = True
                break
            exp_raw.Next()
        self.assertTrue(has_non_analytical_raw, "Raw bend sector should contain non-analytical end caps")

        # Canonicalize sector
        s_can = SheetMetalFolder._canonicalize_bend_sector(s_raw)

        # 1. Verify all faces are Plane or Cylinder
        exp_can = TopExp_Explorer(SheetMetalFolder._to_occ_shape(s_can), TopAbs_FACE)
        face_types = []
        while exp_can.More():
            st = BRepAdaptor_Surface(exp_can.Current(), True).GetType()
            face_types.append(st)
            self.assertIn(
                st,
                (GeomAbs_Plane, GeomAbs_Cylinder),
                f"Canonicalized face has unexpected surface type: {st}"
            )
            exp_can.Next()

        self.assertEqual(face_types.count(GeomAbs_Cylinder), 2, "Bend sector must have exactly 2 cylindrical faces")
        self.assertEqual(face_types.count(GeomAbs_Plane), 4, "Bend sector must have exactly 4 planar faces")

        # 2. Verify volume preservation
        gp_raw = GProp_GProps()
        brepgprop.VolumeProperties(SheetMetalFolder._to_occ_shape(s_raw), gp_raw)
        v_raw = gp_raw.Mass()

        gp_can = GProp_GProps()
        brepgprop.VolumeProperties(SheetMetalFolder._to_occ_shape(s_can), gp_can)
        v_can = gp_can.Mass()

        self.assertAlmostEqual(v_raw, v_can, places=4, msg="Volume must be preserved after canonicalization")

        # 3. Verify BRep validity
        analyzer = BRepCheck_Analyzer(SheetMetalFolder._to_occ_shape(s_can))
        self.assertTrue(analyzer.IsValid(), "Canonicalized bend sector must be topologically valid")

if __name__ == "__main__":
    unittest.main()
