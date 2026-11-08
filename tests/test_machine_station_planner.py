"""Regression tests: longitudinal stations must follow placed geometry."""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.machine_station_planner import plan_machine_stations


class Identity:
    def point(self, p):
        return p


class Placement:
    def __init__(self, shift):
        self.shift = shift

    def apply(self, xyz):
        return xyz[0] + self.shift, xyz[1], xyz[2]


class MachineStationPlannerTests(unittest.TestCase):
    def state(self):
        bend = SimpleNamespace(
            fixed_panel="P", axis_xyz=((0, 0, 0), (100, 0, 0)))
        return SimpleNamespace(
            bends={"B": bend}, graph=SimpleNamespace(panels={"P"}),
            transforms={"P": Identity()},
        )

    def test_machine_placement_shift_is_applied_once(self):
        state = self.state()
        panel = {"P": [(10, 0), (20, 0), (20, 5)]}
        a = plan_machine_stations(state, panel, Placement(0), "B")
        b = plan_machine_stations(state, panel, Placement(200), "B")
        self.assertEqual(a.panel_vertex_count, 3)
        self.assertEqual(b.bend_span_xm, (200, 300))
        self.assertEqual([round(x - y, 6) for x, y in zip(b.stations_xm, a.stations_xm)],
                         [200] * len(a.stations_xm))
        self.assertIn(210, b.stations_xm)
        self.assertIn(220, b.stations_xm)

    def test_tool_end_samples_come_from_verified_spans_only(self):
        state = self.state()
        a = plan_machine_stations(state, {}, Placement(0), "B",
                                  verified_punch_span_xm=(-40, 40),
                                  verified_die_span_xm=(-60, 60))
        self.assertIn(-40.0, a.stations_xm)
        self.assertIn(60.5, a.stations_xm)
        self.assertTrue(a.punch_span_verified)
        self.assertTrue(a.die_span_verified)
        b = plan_machine_stations(state, {}, Placement(0), "B")
        self.assertFalse(b.punch_span_verified)
        self.assertFalse(b.die_span_verified)
        self.assertNotIn(60.5, b.stations_xm)

    def test_tilted_active_axis_fails_closed(self):
        state = self.state()
        state.bends["B"].axis_xyz = ((0, 0, 0), (100, 1, 0))
        with self.assertRaisesRegex(ValueError, "not aligned"):
            plan_machine_stations(state, {}, Placement(0), "B")

    def test_invalid_span_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid verified"):
            plan_machine_stations(self.state(), {}, Placement(0), "B",
                                  verified_die_span_xm=(10, -10))

    def test_station_overflow_does_not_silently_drop(self):
        with self.assertRaisesRegex(ValueError, "probe limit exceeded"):
            plan_machine_stations(self.state(), {}, Placement(0), "B", max_stations=1)

    def test_report_serializable(self):
        p = plan_machine_stations(self.state(), {}, Placement(0), "B")
        data = p.to_dict()
        self.assertEqual(data["evidence"], "STATION_SELECTION_ONLY_NOT_CONTINUOUS_PROOF")
        self.assertEqual(data["bend_span_xm"], [0, 100])


if __name__ == "__main__":
    unittest.main()
