from dataclasses import replace
from types import SimpleNamespace
import unittest

from freecad.bendseq.core.kinematics2d.backgauge_2d import BackgaugeModel2D, BackgaugePosition2D
from freecad.bendseq.core.kinematics2d.station_backgauge import resolve_station_backgauge


class StationBackgaugeTests(unittest.TestCase):
    def setUp(self):
        self.model = BackgaugeModel2D(
            10, 5, {}, automatic=True, cad_station_xm_mm=-20,
            cad_second_station_xm_mm=20, z_min_mm=-3, z_max_mm=20,
        )
        self.faces = (SimpleNamespace(vertices=(
            (45, 80, 0), (45, 120, 0), (45, 120, 2), (45, 80, 2),
        )),)

    def resolve(self, *, model=None, bounds=(-150, 150), obstacles=()):
        return resolve_station_backgauge(
            model or self.model, "B01", self.faces, bounds,
            gauge_bounds=(-1, 5, -3, 3, -2, 3), face_z_span=(-1, 1),
            die_obstacles=obstacles,
        )

    def test_fingers_follow_sheet_in_machine_x(self):
        result = self.resolve()
        self.assertEqual(result.stations, (90, 110))
        self.assertEqual(result.contact, (45, 1))

    def test_dies_keep_their_own_longitudinal_and_transverse_bounds(self):
        obstacles = (
            (((44, -10), (49, 0)), ((-120, -80),)),
            (((0, -10), (10, 0)), ((80, 120),)),
        )
        self.assertEqual(self.resolve(obstacles=obstacles).contact, (45, 1))
        # A neighboring installed die overlaps the right finger. The
        # required height clears the die but cannot still reach the edge.
        neighbor = (((44, -10), (49, 2)), ((111, 130),))
        result = self.resolve(obstacles=(*obstacles, neighbor))
        self.assertIsNone(result.contact)
        self.assertEqual(result.reason, "gauge_z_contact_unreachable")

    def test_rejects_outside_x_and_z_travel(self):
        self.assertEqual(self.resolve(bounds=(-150, 112)).reason, "stations_gauge_outside_layout")
        self.assertEqual(self.resolve(model=replace(self.model, z_max_mm=-1)).reason,
                         "gauge_z_contact_unreachable")

    def test_manual_fingers_do_not_translate_implicitly(self):
        manual = replace(self.model, automatic=False,
                         positions={"B01": BackgaugePosition2D(50, 1)})
        self.assertEqual(self.resolve(model=manual).reason, "stations_gauge_no_edge")
        manual = replace(manual, cad_station_xm_mm=90, cad_second_station_xm_mm=110)
        self.assertEqual(self.resolve(model=manual).contact, (45, 1))
