"""
test_sheetmetal_params_api.py - Dedicated isolated unit tests for SheetMetalApiClient public parameter API (Commit A)
"""

import os
import sys
import unittest
import math

sys.path.insert(0, '/Users/badmc/Desktop/SMBS')
sys.path.insert(0, '/Users/badmc/Desktop/SMBS/tests')
sys.path.insert(0, '/Users/badmc/.gemini/antigravity-ide/brain/f7f8efb2-5153-4bda-9d68-1afe2a983f3c/scratch')
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')


from smbs_core.api.sheetmetal_client import SheetMetalApiClient
from test_user_final_concentricity import create_l_bracket_shape_concentric
import FreeCAD
import Part
import tempfile
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs


def create_l_bracket_shape(angle_deg: float = 90.0) -> Part.Shape:
    """Creates a concentric L-bracket shape for testing parameter extraction."""
    s0_occ = create_l_bracket_shape_concentric(angle_deg=angle_deg, radius=1.0, thickness=1.5, l1=95.5, l2=47.5)
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
        step_path = tmp.name
    writer = STEPControl_Writer()
    writer.Transfer(s0_occ, STEPControl_AsIs)
    writer.Write(step_path)
    fc_shape = Part.read(step_path)
    os.remove(step_path)
    return fc_shape


class TestSheetMetalParamsApi(unittest.TestCase):

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

    def test_k_factor_reads_from_sheetmetal_preferences(self):
        import FreeCAD
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        params.SetFloat("manualKFactor", 0.30)
        try:
            ba = self.client.get_bend_allowance(1.0, 1.5, 90.0)
            # BA = pi * (1.0 + 0.30 * 1.5) * 90 / 180 = 2.2776547
            self.assertAlmostEqual(ba, 2.2777, places=3)
        finally:
            params.SetFloat("manualKFactor", 0.44)

    def test_k_factor_falls_back_to_044_when_preferences_unavailable(self):
        from unittest.mock import patch
        with patch("FreeCAD.ParamGet", side_effect=RuntimeError("no prefs")):
            k, std = self.client._read_k_factor_preferences()
            self.assertAlmostEqual(k, 0.44, places=6)
            self.assertEqual(std, "ansi")

    def test_k_factor_override_ignores_preferences(self):
        import FreeCAD
        params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        params.SetFloat("manualKFactor", 0.30)
        try:
            ba = self.client.get_bend_allowance(
                1.0, 1.5, 90.0, k_factor_override=0.44
            )
            self.assertAlmostEqual(ba, 2.6075219, places=6)
        finally:
            params.SetFloat("manualKFactor", 0.44)


    def test_get_thickness_returns_1_5_for_known_L_bracket(self):
        s0 = create_l_bracket_shape(angle_deg=90.0)
        thickness = self.client.get_thickness(s0)
        self.assertAlmostEqual(thickness, 1.5, places=3)

    def test_get_bend_allowance_90deg_ansi_k044(self):
        ba = self.client.get_bend_allowance(radius=1.0, thickness=1.5, angle_deg=90.0, standard="ansi")
        # Formula: pi * (1.0 + 0.44 * 1.5) * 90 / 180 = 2.6075219
        self.assertAlmostEqual(ba, 2.6075219, places=6)

    def test_get_bend_allowance_45deg_ansi_k044(self):
        ba = self.client.get_bend_allowance(radius=1.0, thickness=1.5, angle_deg=45.0, standard="ansi")
        # Formula: pi * (1.0 + 0.44 * 1.5) * 45 / 180 = 1.3037609
        self.assertAlmostEqual(ba, 1.3037609, places=6)

    def test_get_bend_angle_from_cylinder_90deg(self):
        s0 = create_l_bracket_shape(angle_deg=90.0)
        cyl_faces = [f for f in s0.Faces if getattr(f.Surface, 'TypeId', '') == 'Part::GeomCylinder']
        self.assertGreater(len(cyl_faces), 0)
        angle_deg, is_down = self.client.get_bend_angle_and_direction(cyl_faces[0])
        self.assertAlmostEqual(angle_deg, 90.0, places=2)

    def test_get_bend_angle_uses_fallback_for_full_cylinder(self):
        # Full cylinder with u_span ≈ 2π
        cyl_full = Part.makeCylinder(1.0, 50.0)
        face = cyl_full.Faces[0]
        angle_deg, is_down = self.client.get_bend_angle_and_direction(face)
        # Verify fallback does not crash and returns a valid float angle
        self.assertIsInstance(angle_deg, float)
        self.assertAlmostEqual(angle_deg, 360.0, places=2)

    def test_get_bend_angle_partial_seam_arc(self):
        # Partial cylinder (90 deg arc)
        cyl_90 = Part.makeCylinder(1.0, 50.0, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 90.0)
        face = cyl_90.Faces[0]
        angle_deg, is_down = self.client.get_bend_angle_and_direction(face)
        self.assertAlmostEqual(angle_deg, 90.0, places=2)

    def test_check_temp_part_step_thickness(self):
        step_path = '/Users/badmc/temp_part.step'
        if not os.path.exists(step_path):
            step_path = '/Users/badmc/Desktop/SMBS/temp_part.step'
        if not os.path.exists(step_path):
            self.skipTest("temp_part.step not found")

        step_shape = Part.Shape()
        step_shape.read(step_path)
        bb = step_shape.BoundBox
        print("\n--- temp_part.step get_thickness check ---")
        print(f"Total faces: {len(step_shape.Faces)}")
        print(f"BoundBox: XLength={bb.XLength:.4f}, YLength={bb.YLength:.4f}, ZLength={bb.ZLength:.4f}, min_dim={min(bb.XLength, bb.YLength, bb.ZLength):.4f}")
        for idx in [0, 5, 10]:

            if idx < len(step_shape.Faces):
                f = step_shape.Faces[idx]
                stype = getattr(f.Surface, 'TypeId', 'Unknown')
                try:
                    t = self.client.get_thickness(step_shape, idx)
                    print(f"face {idx} ({stype}, area={f.Area:.1f}): thickness = {t:.4f}")
                except Exception as e:
                    print(f"face {idx} ({stype}, area={f.Area:.1f}): failed — {e}")
    def test_compare_sm_vs_geom_thickness(self):
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        from smbs_core.Geometry.feature_extractor import FeatureExtractor
        from test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric

        backend = PythonOCCBackend()
        extractor = FeatureExtractor(backend, sm_client=self.client)

        parts_to_test = []
        step_path = '/Users/badmc/temp_part.step'
        if not os.path.exists(step_path):
            step_path = '/Users/badmc/Desktop/SMBS/temp_part.step'
        if os.path.exists(step_path):
            s_step_fc = Part.Shape()
            s_step_fc.read(step_path)
            s_step_occ = backend.load_shape(step_path)
            parts_to_test.append(('temp_part.step', s_step_fc, s_step_occ))

        s_l90_occ = create_l_bracket_shape_concentric(angle_deg=90.0, radius=1.0, thickness=1.5, l1=95.5, l2=47.5)
        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            tmp_l_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(s_l90_occ, STEPControl_AsIs)
        writer.Write(tmp_l_path)
        s_l90_fc = Part.read(tmp_l_path)
        os.remove(tmp_l_path)
        parts_to_test.append(('L-90', s_l90_fc, s_l90_occ))

        s_u90_occ = create_u_bracket_shape_perfect_concentric()
        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            tmp_u_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(s_u90_occ, STEPControl_AsIs)
        writer.Write(tmp_u_path)
        s_u90_fc = Part.read(tmp_u_path)
        os.remove(tmp_u_path)
        parts_to_test.append(('U-90', s_u90_fc, s_u90_occ))

        print("\n--- SM vs geom Thickness Comparison ---")
        for name, fc_shape, occ_shape in parts_to_test:
            t_sm = self.client.get_thickness(fc_shape)
            t_geom = extractor._detect_thickness(occ_shape)
            diff = abs(t_sm - t_geom)
            print(f"{name}: SM={t_sm:.4f}, geom={t_geom:.4f}, diff={diff:.6f}")
            self.assertLess(diff, 1e-3, f"Thickness mismatch for {name}: SM={t_sm}, geom={t_geom}")
        print("----------------------------------------\n")

    def test_extract_features_falls_back_when_sm_client_raises(self):
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        from smbs_core.Geometry.feature_extractor import FeatureExtractor
        from test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric

        class BrokenClient:
            def get_thickness(self, shape):
                raise RuntimeError("Simulated SheetMetal client crash")

        backend = PythonOCCBackend()
        extractor = FeatureExtractor(backend, sm_client=BrokenClient())
        s_u90_occ = create_u_bracket_shape_perfect_concentric()

        features = extractor.extract_features(s_u90_occ)
        self.assertAlmostEqual(features['thickness'], 1.5, places=3)

    def test_compare_sm_vs_geom_multi_thickness(self):
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        from smbs_core.Geometry.feature_extractor import FeatureExtractor

        backend = PythonOCCBackend()
        extractor = FeatureExtractor(backend, sm_client=self.client)

        print("\n--- Multi-Thickness SM vs geom Comparison (t in {0.8, 1.5, 2.0}) ---")
        for t in [0.8, 1.5, 2.0]:
            s_occ = create_l_bracket_shape_concentric(angle_deg=90.0, radius=1.0, thickness=t, l1=95.5, l2=47.5)
            with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
                tmp_path = tmp.name
            writer = STEPControl_Writer()
            writer.Transfer(s_occ, STEPControl_AsIs)
            writer.Write(tmp_path)
            s_fc = Part.read(tmp_path)
            os.remove(tmp_path)

            t_sm = self.client.get_thickness(s_fc)
            t_geom = extractor._detect_thickness(s_occ)
            diff = abs(t_sm - t_geom)
            print(f"t={t:.1f}: SM={t_sm:.4f}, geom={t_geom:.4f}, diff={diff:.6f}")
            self.assertLess(diff, 1e-3, f"Thickness mismatch for t={t}: SM={t_sm}, geom={t_geom}")
        print("-----------------------------------------------------------------------\n")

    def test_get_thickness_raises_on_implausible_face(self):
        from smbs_core.api.sheetmetal_client import SheetMetalApiClientError
        step_path = '/Users/badmc/temp_part.step'
        if not os.path.exists(step_path):
            step_path = '/Users/badmc/Desktop/SMBS/temp_part.step'
        if not os.path.exists(step_path):
            self.skipTest("temp_part.step not found")

        s_step = Part.Shape()
        s_step.read(step_path)
        # Face 5 on temp_part.step has small area (4.1 mm2) and returns implausible 195.0 mm
        with self.assertRaises(SheetMetalApiClientError):
            self.client.get_thickness(s_step, face_idx=5)

    def test_bend_record_with_none_fields_resolves_from_client(self):
        from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
        from smbs_core.Geometry.feature_extractor import FeatureExtractor
        from test_sheetmetal_ubracket import create_u_bracket_shape_perfect_concentric

        backend = PythonOCCBackend()
        extractor = FeatureExtractor(backend, sm_client=self.client)
        s_u90_occ = create_u_bracket_shape_perfect_concentric()

        features = extractor.extract_features(s_u90_occ)
        self.assertIn("records", features)
        self.assertGreater(len(features["records"]), 0)

        # Ensure BendRecord.thickness and radius are None
        for b_id, rec in features["records"].items():
            self.assertIsNone(rec.thickness)
            self.assertIsNone(rec.radius)
            self.assertIsNotNone(rec.signed_angle)
            self.assertIsNotNone(rec.length)

        # But features['bends'][b_id]['thickness'] and ['radius'] still available
        for b_id, info in features["bends"].items():
            self.assertAlmostEqual(info["thickness"], 1.5, places=3)
            self.assertAlmostEqual(info["radius"], 1.0, places=3)


if __name__ == '__main__':
    unittest.main()




