"""Backgauge vertical-axis hard travel limit regression coverage."""

import unittest

from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D,
    BackgaugePosition2D,
    BackgaugeZTravelError,
)


def model(**overrides):
    return BackgaugeModel2D(
        width_s_mm=10.0,
        height_z_mm=10.0,
        positions={},
        **overrides,
    )


class BackgaugeZLimitsTests(unittest.TestCase):
    def test_edges_inclusive(self):
        gauge = model()
        for z in (-20.0, 0.0, 20.0):
            gauge.validate_z_travel(BackgaugePosition2D(50.0, z), "B01")

    def test_outside_range_rejected(self):
        gauge = model()
        for z in (-383.94, -20.01, 20.01, 150.0, 248.75):
            with self.subTest(z=z):
                with self.assertRaisesRegex(BackgaugeZTravelError, "BACKGAUGE_Z_OUT_OF_RANGE"):
                    gauge.validate_z_travel(BackgaugePosition2D(50.0, z), "B01")

    def test_roundtrip_and_custom_limits(self):
        gauge = model(z_min_mm=-10.0, z_max_mm=15.0)
        loaded = BackgaugeModel2D.from_dict(gauge.to_dict())
        self.assertEqual(loaded.z_min_mm, -10.0)
        self.assertEqual(loaded.z_max_mm, 15.0)
        with self.assertRaises(BackgaugeZTravelError):
            loaded.validate_z_travel(BackgaugePosition2D(50.0, 16.0), "B01")

    def test_legacy_config_uses_default_range(self):
        data = model().to_dict()
        del data["z_min_mm"]
        del data["z_max_mm"]
        loaded = BackgaugeModel2D.from_dict(data)
        self.assertEqual((loaded.z_min_mm, loaded.z_max_mm), (-20.0, 20.0))

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            model(z_min_mm=20.0, z_max_mm=-20.0)


if __name__ == "__main__":
    unittest.main()
