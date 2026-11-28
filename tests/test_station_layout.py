from dataclasses import replace
import unittest

from freecad.bendseq.core.geometry_backend.io.clamp_validator import ClampConfig
from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup
from freecad.bendseq.core.kinematics2d.station_layout import (
    StationLayoutError, auto_layout_stations, calculate_station_segments, validate_station_layout,
)


def make_station(name, x, **kwargs):
    return BendingStation(name, name, "P", "D", x, ((-25, 25),), ((-25, 25),), **kwargs)


class StationLayoutTests(unittest.TestCase):
    def setUp(self):
        self.stock = {key: [{"length_mm": 50, "quantity": 2}]
                      for key in (("punch", "P"), ("die", "D"))}
        self.clamps = ClampConfig(20, 0, (-50, 50), 0.4, 0.1, 1,
                                  repositionable=False)
        self.setup = StationSetup((-100, 100), (make_station("A", -50),
                                  make_station("B", 50, enabled=False)), 10)

    def test_disabled_stations_reserve_stock(self):
        self.assertEqual(validate_station_layout(self.setup, self.stock, self.clamps),
                         {key: [] for key in self.stock})
        with self.assertRaisesRegex(StationLayoutError, "Insufficient stock"):
            validate_station_layout(replace(self.setup, stations=(*self.setup.stations,
                                    make_station("C", 0))), self.stock, self.clamps)

    def test_manual_gap_and_unsupported_punch(self):
        first, second = self.setup.stations
        with self.assertRaisesRegex(StationLayoutError, "gap"):
            validate_station_layout(replace(self.setup, stations=(first, replace(second, x_mm=3))),
                                    self.stock, self.clamps)
        with self.assertRaisesRegex(StationLayoutError, "unsupported"):
            validate_station_layout(replace(self.setup, stations=(replace(first, x_mm=0),)),
                                    self.stock, self.clamps)

    def test_auto_layout_uses_configured_clamps_and_fails_on_length(self):
        staged = replace(self.setup, stations=tuple(replace(s, x_mm=0) for s in self.setup.stations))
        arranged = auto_layout_stations(staged, self.stock, self.clamps)
        validate_station_layout(arranged, self.stock, self.clamps)
        self.assertTrue(arranged.stations[0].x_mm < 0 < arranged.stations[1].x_mm)
        with self.assertRaisesRegex(StationLayoutError, "working length"):
            auto_layout_stations(replace(staged, machine_x_mm=(-50, 50)), self.stock, self.clamps)

    def test_segment_replanning_reserves_other_station_stock(self):
        first = self.setup.stations[0]
        self.assertEqual(calculate_station_segments(self.setup, first, 50, self.stock, self.clamps),
                         first)
        with self.assertRaisesRegex(StationLayoutError, "NO_INVENTORY_COMBINATION"):
            calculate_station_segments(self.setup, first, 90, self.stock, self.clamps)
