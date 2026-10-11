"""
test_sheetmetal_api_client.py - Integration tests for SheetMetalApiClient
"""

import os
import sys
import unittest
import math

sys.path.insert(0, '/Users/badmc/Desktop/SMBS')
sys.path.insert(0, '/Users/badmc/.gemini/antigravity-ide/brain/f7f8efb2-5153-4bda-9d68-1afe2a983f3c/scratch')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.api.sheetmetal_client import SheetMetalApiClient, SheetMetalApiClientError
from test_user_final_concentricity import create_l_bracket_shape_concentric
import FreeCAD
import Part


def create_test_bent_shape(angle_deg: float = 90.0) -> Part.Shape:
    """Creates a bent L-bracket shape for unbending test."""
    s0_occ = create_l_bracket_shape_concentric(angle_deg=angle_deg, radius=1.0, thickness=1.5, l1=95.5, l2=47.5)
    import tempfile
    from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        step_path = tmp.name
    writer = STEPControl_Writer()
    writer.Transfer(s0_occ, STEPControl_AsIs)
    writer.Write(step_path)
    fc_shape = Part.read(step_path)
    os.remove(step_path)
    return fc_shape


class TestSheetMetalApiClient(unittest.TestCase):

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


    def test_unfold_90deg_numerical_precision(self):
        fc_shape = create_test_bent_shape(90.0)
        unfolded_solid = self.client.unfold_shape_headless(fc_shape)
        self.assertIsNotNone(unfolded_solid)
        self.assertTrue(hasattr(unfolded_solid, 'BoundBox'))
        bb = unfolded_solid.BoundBox
        w_flat = max(bb.XMax - bb.XMin, bb.YMax - bb.YMin, bb.ZMax - bb.ZMin)
        
        # Expected SheetMetal ANSI BA (K=0.44) for 90°: 95.5 + 2.607522 + 47.5 = 145.607522
        self.assertAlmostEqual(w_flat, 145.607522, delta=1e-3)

        # Check raw B-Rep face topology
        faces = unfolded_solid.Faces
        cyls = [f for f in faces if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertEqual(len(cyls), 0)
        self.assertGreaterEqual(len(faces), 8)
        self.assertNotEqual(len(faces), 6) # Not a synthetic 6-face box

    def test_unfold_plus_minus_45deg_symmetry(self):
        shape_plus = create_test_bent_shape(45.0)
        shape_minus = create_test_bent_shape(-45.0)

        unfolded_plus = self.client.unfold_shape_headless(shape_plus)
        unfolded_minus = self.client.unfold_shape_headless(shape_minus)

        bb_plus = unfolded_plus.BoundBox
        bb_minus = unfolded_minus.BoundBox

        w_flat_plus = max(bb_plus.XMax - bb_plus.XMin, bb_plus.YMax - bb_plus.YMin, bb_plus.ZMax - bb_plus.ZMin)
        w_flat_minus = max(bb_minus.XMax - bb_minus.XMin, bb_minus.YMax - bb_minus.YMin, bb_minus.ZMax - bb_minus.ZMin)

        # Strict symmetry check: W_flat for +45° and -45° must match within 1e-3 mm
        self.assertAlmostEqual(w_flat_plus, w_flat_minus, delta=1e-3)
        self.assertAlmostEqual(unfolded_plus.Area, unfolded_minus.Area, delta=1e-2)

    def test_unfold_part_gui_simulation(self):
        doc = FreeCAD.newDocument("GUIDocTest")
        try:
            fc_shape = create_test_bent_shape(90.0)
            part_obj = doc.addObject("Part::Feature", "TestPart")
            part_obj.Shape = fc_shape
            doc.recompute()

            target_face = "Face1"
            try:
                unfold_obj = self.client.unfold_part(doc.Name, part_obj.Name, target_face)
                self.assertIsNotNone(unfold_obj)
            except SheetMetalApiClientError as e:
                # Expected when FreeCADGui is present without display/GUI loop
                self.assertIn("FreeCADGui", str(e))
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_sheetmetal_public_parameter_api(self):
        fc_shape = create_test_bent_shape(90.0)
        
        # 1. Thickness
        t = self.client.get_thickness(fc_shape)
        self.assertAlmostEqual(t, 1.5, delta=1e-3)

        # 2. K-Factor
        kf = self.client.get_k_factor(radius=1.0, thickness=1.5, standard="ansi")
        self.assertAlmostEqual(kf, 0.44, delta=1e-3)

        # 3. Bend Allowance
        ba = self.client.get_bend_allowance(radius=1.0, thickness=1.5, angle_deg=90.0, standard="ansi")
        self.assertAlmostEqual(ba, 2.607522, delta=1e-3)

        # 4. Bend Angle and Direction
        cyl_faces = [f for f in fc_shape.Faces if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertGreater(len(cyl_faces), 0)
        angle_deg, is_down = self.client.get_bend_angle_and_direction(cyl_faces[0])
        self.assertAlmostEqual(angle_deg, 90.0, delta=1e-1)


if __name__ == '__main__':
    unittest.main()
