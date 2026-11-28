from dataclasses import replace
import unittest

from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup


def station(station_id="S1", x=0):
    return BendingStation(station_id, station_id, "P1", "D1", x,
                          ((-50, -25), (-25, 0), (10, 35)), ((-30, 40),))


class BendingStationTests(unittest.TestCase):
    def test_machine_coordinates_preserve_actual_tool_gaps(self):
        tool = station(x=100)
        self.assertEqual(tool.occupied_interval(), (50, 140))
        self.assertEqual(tool.working_intervals(), ((70, 100), (110, 135)))
        self.assertEqual(tool.segments("punch"), {25.0: 3})

    def test_independent_stage_assignments(self):
        first, second = station(), replace(station("S2", 125), die_id="D2")
        setup = StationSetup((-300, 300), (first, second), 10,
                             (("B01.1", "S1"), ("B01.2", "S2")))
        self.assertEqual(setup.assigned_station("B01.1"), first)
        self.assertEqual(setup.assigned_station("B01.2"), second)
        self.assertIsNone(setup.assigned_station("B02"))
        with self.assertRaisesRegex(ValueError, "disabled"):
            replace(setup, stations=(first, replace(second, enabled=False)))

    def test_duplicate_ids_and_overlapping_segments_rejected(self):
        first = station()
        with self.assertRaisesRegex(ValueError, "Duplicate station"):
            StationSetup((-300, 300), (first, first))
        with self.assertRaisesRegex(ValueError, "overlap"):
            replace(first, punch_spans=((-25, 0), (-10, 10)))
