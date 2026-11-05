"""D6.3 volume and CAD model consistency validation tests."""
import math
import unittest

from smbs_core.kinematics2d.native_volume_gate import (
    volume_consistency,
    ideal_constant_thickness_bend_drift_mm3,
    polygonal_bend_discretization_error_mm3,
    predict_cad_consistent_volume_mm3,
    cad_volume_consistency,
    multi_bend_cad_volume_consistency,
)


class NativeVolumeGateTests(unittest.TestCase):
    def test_flat_constant_volume_passes(self):
        result = volume_consistency([29253.982, 29253.982, 29253.982])
        self.assertTrue(result["pass"])
        self.assertEqual(result["maximum_relative_drift_percent"], 0)

    def test_actual_freecad_20261105_report_fails(self):
        # Values from actual FreeCAD 1.1.3 JSON, not synthetic geometry.
        values = [29253.98223686155, 29271.65240185775,
                  29289.314804830967, 29306.96168405041,
                  29324.585278369843]
        result = volume_consistency(values)
        self.assertFalse(result["pass"])
        self.assertAlmostEqual(result["maximum_absolute_drift_mm3"], 70.60304150829324)
        self.assertAlmostEqual(result["maximum_relative_drift_percent"], 0.2413450617992436)

    def test_bend_zone_drift_explained_by_k_factor(self):
        predicted = ideal_constant_thickness_bend_drift_mm3(
            thickness_mm=1.5, k_factor=0.4,
            bend_length_mm=200, angle_deg=90
        )
        self.assertAlmostEqual(predicted, 70.68583470577033, places=6)

    def test_k_half_concentric_sector_zero_drift(self):
        v = ideal_constant_thickness_bend_drift_mm3(
            thickness_mm=1.5, k_factor=0.5, bend_length_mm=200, angle_deg=90
        )
        self.assertEqual(v, 0.0)

    def test_rejects_nan_and_negative_tolerances(self):
        for volumes in ([], [1., float("nan")], [1., 0.], [1., -1.]):
            with self.assertRaises(ValueError):
                volume_consistency(volumes)
        with self.assertRaises(ValueError):
            volume_consistency([1.], relative_tolerance=-1)

    # --- D6.3.1 CAD Volume Consistency Tests ---

    def test_polygonal_discretization_error_zero_at_flat(self):
        eps = polygonal_bend_discretization_error_mm3(
            inner_radius_mm=1.0, outer_radius_mm=2.5,
            angle_deg=0.0, bend_length_mm=200.0, segments=64
        )
        self.assertEqual(eps, 0.0)

    def test_polygonal_discretization_error_positive_at_angle(self):
        eps64 = polygonal_bend_discretization_error_mm3(
            inner_radius_mm=1.0, outer_radius_mm=2.5,
            angle_deg=90.0, bend_length_mm=200.0, segments=64
        )
        eps128 = polygonal_bend_discretization_error_mm3(
            inner_radius_mm=1.0, outer_radius_mm=2.5,
            angle_deg=90.0, bend_length_mm=200.0, segments=128
        )
        self.assertAlmostEqual(eps64, 0.082793, places=5)
        self.assertAlmostEqual(eps128, 0.020699, places=5)
        self.assertGreater(eps64, eps128)

    def test_cad_volume_consistency_real_freecad_values_pass(self):
        actual_volumes = [
            29253.98223686155,
            29271.65240185775,
            29289.314804830967,
            29306.96168405041,
            29324.585278369843,
        ]
        angles = [0.0, 22.5, 45.0, 67.5, 90.0]
        res = cad_volume_consistency(
            actual_volumes,
            angles_deg=angles,
            thickness_mm=1.5,
            k_factor=0.4,
            bend_length_mm=200.0,
            inner_radius_mm=1.0,
            segments=64,
        )
        self.assertTrue(res["pass"])
        self.assertEqual(res["status"], "PASS")
        self.assertLess(res["maximum_absolute_residual_mm3"], 1e-4)

    def test_cad_volume_consistency_fails_on_incorrect_volume(self):
        corrupted_volumes = [
            29253.98223686155,
            29271.65240185775,
            29289.314804830967,
            29306.96168405041,
            29400.0,
        ]
        angles = [0.0, 22.5, 45.0, 67.5, 90.0]
        res = cad_volume_consistency(
            corrupted_volumes,
            angles_deg=angles,
            thickness_mm=1.5,
            k_factor=0.4,
            bend_length_mm=200.0,
            inner_radius_mm=1.0,
            segments=64,
        )
        self.assertFalse(res["pass"])
        self.assertEqual(res["status"], "FAIL")
        self.assertGreater(res["maximum_absolute_residual_mm3"], 70.0)

    def test_cad_volume_consistency_down_bend_negative_angles(self):
        angles = [0.0, -22.5, -45.0, -67.5, -90.0]
        baseline = 10000.0
        volumes = [
            predict_cad_consistent_volume_mm3(
                baseline_flat_volume_mm3=baseline,
                thickness_mm=2.0,
                k_factor=0.35,
                bend_length_mm=150.0,
                angle_deg=a,
                inner_radius_mm=2.0,
                segments=64,
            )
            for a in angles
        ]
        res = cad_volume_consistency(
            volumes,
            angles_deg=angles,
            thickness_mm=2.0,
            k_factor=0.35,
            bend_length_mm=150.0,
            inner_radius_mm=2.0,
            segments=64,
        )
        self.assertTrue(res["pass"])
        self.assertLess(res["maximum_absolute_residual_mm3"], 1e-8)

    def test_synthetic_k_factors(self):
        for k in (0.3, 0.4, 0.5):
            for t in (1.0, 1.5, 3.0):
                for r in (0.5, 1.0, 2.5):
                    for L in (50.0, 200.0):
                        v_pred = predict_cad_consistent_volume_mm3(
                            baseline_flat_volume_mm3=10000.0,
                            thickness_mm=t,
                            k_factor=k,
                            bend_length_mm=L,
                            angle_deg=90.0,
                            inner_radius_mm=r,
                            segments=64,
                        )
                        self.assertTrue(math.isfinite(v_pred))
                        self.assertGreater(v_pred, 0.0)

    def test_input_validation_cad_volume_consistency(self):
        with self.assertRaises(ValueError):
            cad_volume_consistency([100.0, 105.0], angles_deg=[0.0],
                                   thickness_mm=1.0, k_factor=0.4, bend_length_mm=100.0)
        with self.assertRaises(ValueError):
            cad_volume_consistency([100.0], angles_deg=[0.0],
                                   thickness_mm=-1.0, k_factor=0.4, bend_length_mm=100.0)
        with self.assertRaises(ValueError):
            cad_volume_consistency([100.0], angles_deg=[0.0],
                                   thickness_mm=1.0, k_factor=0.4, bend_length_mm=100.0,
                                   segments=64)

    # --- D6.3.2 Multi-Bend CAD Volume Consistency Tests ---

    def test_multi_bend_cad_volume_consistency_z_fixture(self):
        # Actual recorded values from Z-(ok).FCStd in FreeCAD 1.1.3
        actual_volumes = [
            22507.9644737231,   # flat
            22543.29699863483,  # B01=45, B02=0
            22578.567515231393, # B01=90, B02=0
            22613.900040143126, # B01=90, B02=-45
            22649.170556739704, # B01=90, B02=-90
        ]
        states = [
            {"B01": 0.0, "B02": 0.0},
            {"B01": 45.0, "B02": 0.0},
            {"B01": 90.0, "B02": 0.0},
            {"B01": 90.0, "B02": -45.0},
            {"B01": 90.0, "B02": -90.0},
        ]
        bends_params = {
            "B01": {"thickness_mm": 1.5, "k_factor": 0.4, "bend_length_mm": 200.0, "inner_radius_mm": 1.0},
            "B02": {"thickness_mm": 1.5, "k_factor": 0.4, "bend_length_mm": 200.0, "inner_radius_mm": 1.0},
        }
        res = multi_bend_cad_volume_consistency(
            actual_volumes,
            state_angles_list=states,
            bends_params=bends_params,
            segments=64,
        )
        self.assertTrue(res["pass"])
        self.assertEqual(res["status"], "PASS")
        self.assertLess(res["maximum_absolute_residual_mm3"], 1e-4)

    def test_multi_bend_cad_volume_consistency_fails_on_corrupt(self):
        actual_volumes = [
            22507.9644737231,
            22543.29699863483,
            22578.567515231393,
            22613.900040143126,
            22700.0,  # Corrupted
        ]
        states = [
            {"B01": 0.0, "B02": 0.0},
            {"B01": 45.0, "B02": 0.0},
            {"B01": 90.0, "B02": 0.0},
            {"B01": 90.0, "B02": -45.0},
            {"B01": 90.0, "B02": -90.0},
        ]
        bends_params = {
            "B01": {"thickness_mm": 1.5, "k_factor": 0.4, "bend_length_mm": 200.0, "inner_radius_mm": 1.0},
            "B02": {"thickness_mm": 1.5, "k_factor": 0.4, "bend_length_mm": 200.0, "inner_radius_mm": 1.0},
        }
        res = multi_bend_cad_volume_consistency(
            actual_volumes,
            state_angles_list=states,
            bends_params=bends_params,
            segments=64,
        )
        self.assertFalse(res["pass"])
        self.assertEqual(res["status"], "FAIL")
        self.assertGreater(res["maximum_absolute_residual_mm3"], 50.0)


if __name__ == "__main__":
    unittest.main()
