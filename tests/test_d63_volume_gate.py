"""D6.3 reject closed but non-volume-preserving reconstructed sheets."""
import math
import unittest

from smbs_core.kinematics2d.native_volume_gate import (
    volume_consistency,
    ideal_constant_thickness_bend_drift_mm3,
)


class NativeVolumeGateTests(unittest.TestCase):
    def test_flat_constant_volume_passes(self):
        result=volume_consistency([29253.982,29253.982,29253.982])
        self.assertTrue(result["pass"])
        self.assertEqual(result["maximum_relative_drift_percent"],0)

    def test_actual_freecad_20261105_report_fails(self):
        # Values from actual FreeCAD 1.1.3 JSON, not synthetic geometry.
        values=[29253.98223686155,29271.65240185775,
                29289.314804830967,29306.96168405041,
                29324.585278369843]
        result=volume_consistency(values)
        self.assertFalse(result["pass"])
        self.assertAlmostEqual(result["maximum_absolute_drift_mm3"],70.60304150829324)
        self.assertAlmostEqual(result["maximum_relative_drift_percent"],0.2413450617992436)

    def test_bend_zone_drift_explained_by_k_factor(self):
        predicted=ideal_constant_thickness_bend_drift_mm3(
            thickness_mm=1.5,k_factor=0.4,
            bend_length_mm=200,angle_deg=90
        )
        self.assertAlmostEqual(predicted,70.68583470577033,places=6)

    def test_k_half_concentric_sector_zero_drift(self):
        v=ideal_constant_thickness_bend_drift_mm3(
            thickness_mm=1.5,k_factor=0.5,bend_length_mm=200,angle_deg=90
        )
        self.assertEqual(v,0.0)

    def test_rejects_nan_and_negative_tolerances(self):
        for volumes in ([], [1.,float("nan")], [1.,0.], [1.,-1.]):
            with self.assertRaises(ValueError):
                volume_consistency(volumes)
        with self.assertRaises(ValueError):
            volume_consistency([1.],relative_tolerance=-1)


if __name__=="__main__":
    unittest.main()
