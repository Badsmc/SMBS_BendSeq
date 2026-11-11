"""Contract tests for lossless, fail-safe sampled 2D collision tracing."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.preliminary_screening import (
    _record_planar_tool_interference,
)


class PlanarCollisionTraceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.checker = SimpleNamespace(
            _trace_writer=object(),
            _trace=lambda event, **kwargs: self.events.append(
                (event, kwargs)
            ),
        )
        self.section = {
            "other_panel_polygons": [
                ("P07", ((0., 0.), (3., 0.), (3., 2.), (0., 2.)))
            ],
        }
        self.profile = (
            ((1., 0.5), (4., 0.5), (4., 3.), (1., 3.)),
            (((1.2, 1.2), (1.8, 1.2), (1.8, 1.8)),),
        )
        self.tooling = SimpleNamespace(
            punch_polygons_machine=lambda *, arc_segments: self.profile,
            die_polygons_machine=lambda *, arc_segments: self.profile,
        )
        self.finding = SimpleNamespace(
            sheet_component="P07",
            tool="PUNCH",
            reason="proper_material_boundary_crossing",
        )

    def record(self, **kwargs):
        _record_planar_tool_interference(
            self.checker, self.section, self.tooling,
            self.finding, bend_id="B04", phase="BENDING",
            angle_fraction=0.5, angle_deg=45.,
            ram_z_mm=12.3, tolerance_mm=0.02, **kwargs,
        )

    def test_captures_both_exact_input_polygons_and_machine_sample(self):
        self.record()
        self.assertEqual(len(self.events), 1)
        name, record = self.events[0]
        self.assertEqual(name, "PLANAR_TOOL_INTERFERENCE")
        self.assertEqual(record["bend_id"], "B04")
        self.assertEqual(record["sheet_component"], "P07")
        self.assertEqual(record["angle_fraction"], .5)
        self.assertEqual(record["ram_z_mm"], 12.3)
        self.assertEqual(record["sheet_polygon"]["vertices_count"], 4)
        self.assertEqual(
            record["sheet_polygon"]["bbox_sz_mm"], [0., 0., 3., 2.]
        )
        self.assertEqual(record["tool_outer_polygon"]["vertices_count"], 4)
        self.assertEqual(len(record["tool_holes"]), 1)

    def test_recording_failure_cannot_remove_or_change_collision_result(self):
        self.tooling.punch_polygons_machine = lambda **kw: 1 / 0
        self.record()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], "PLANAR_TOOL_TRACE_FAILED")
        self.assertIn("ZeroDivisionError", self.events[0][1]["reason"])

    def test_disabled_trace_does_not_request_tool_polygons(self):
        self.checker._trace_writer = None
        self.tooling.punch_polygons_machine = lambda **kw: 1 / 0
        self.record()
        self.assertFalse(self.events)


if __name__ == "__main__":
    unittest.main()
