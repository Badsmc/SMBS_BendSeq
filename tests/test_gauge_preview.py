"""Headless tests for the visual-only real-FCStd gauge position model."""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D,
    BackgaugePosition2D,
)
from smbs_core.kinematics2d.gauge_preview import (
    bend_to_blank_edge_mm,
    local_bend_dimension_mm,
    visual_gauge_target,
)


class GaugePreviewTests(unittest.TestCase):
    def setUp(self):
        self.outline = ((0, 0), (100, 0), (100, 50), (0, 50))
        self.axis = ((10, 0), (10, 50))
        self.left_panel = ((0, 0), (9, 0), (9, 50), (0, 50))
        self.right_panel = ((11, 0), (50, 0), (50, 50), (11, 50))

    def test_rear_edge_is_outer_blank_not_partition_line(self):
        self.assertAlmostEqual(
            bend_to_blank_edge_mm(self.axis, self.left_panel, self.outline),
            10.0,
        )
        self.assertAlmostEqual(
            bend_to_blank_edge_mm(self.axis, self.right_panel, self.outline),
            90.0,
        )

    def test_reverse_axis_direction_does_not_change_distance(self):
        self.assertAlmostEqual(
            bend_to_blank_edge_mm(tuple(reversed(self.axis)), self.left_panel, self.outline),
            10.0,
        )

    def test_diagonal_axis_has_true_perpendicular_distance(self):
        axis = ((0, 0), (10, 10))
        outline = ((-10, -10), (20, -10), (20, 20), (-10, 20))
        panel = ((0, 10), (0, 15), (5, 15))
        self.assertAlmostEqual(
            bend_to_blank_edge_mm(axis, panel, outline),
            15.0 * math.sqrt(2.0),
            places=5,
        )

    def test_no_edge_or_ambiguous_side_fails_without_fake_distance(self):
        with self.assertRaisesRegex(ValueError, "fixed-panel side"):
            bend_to_blank_edge_mm(self.axis, ((5, 10), (15, 10), (10, 30)), self.outline)
        with self.assertRaises(ValueError):
            bend_to_blank_edge_mm(self.axis, self.left_panel, ((20, 20), (30, 20), (30, 30)))

    def test_two_sequential_bends_show_10_then_22_not_32(self):
        first = ((10, 0), (10, 50))
        second = ((32, 0), (32, 50))
        self.assertAlmostEqual(
            local_bend_dimension_mm(first, self.outline, (second,)), 10.0,
        )
        self.assertAlmostEqual(
            local_bend_dimension_mm(second, self.outline, (first,)), 22.0,
        )
        # The pre-existing gauge's absolute reach remains separate.
        self.assertAlmostEqual(
            bend_to_blank_edge_mm(second, self.left_panel, self.outline), 32.0,
        )

    def test_multiple_bends_use_nearest_local_boundary(self):
        first = ((10, 0), (10, 50))
        second = ((32, 0), (32, 50))
        third = ((70, 0), (70, 50))
        self.assertAlmostEqual(
            local_bend_dimension_mm(first, self.outline, (second, third)), 10.0,
        )
        self.assertAlmostEqual(
            local_bend_dimension_mm(second, self.outline, (first, third)), 22.0,
        )
        # Third bend is nearer the right outline at 100 mm than the
        # second bend at 32 mm: it is a local 30 mm flange.
        self.assertAlmostEqual(
            local_bend_dimension_mm(third, self.outline, (first, second)), 30.0,
        )

    def test_local_dimensions_ignore_bend_axis_reversal(self):
        first = ((10, 0), (10, 50))
        second = ((32, 0), (32, 50))
        self.assertAlmostEqual(
            local_bend_dimension_mm(tuple(reversed(second)), self.outline,
                                    (tuple(reversed(first)),)), 22.0,
        )

    def test_unrelated_perpendicular_bend_is_not_a_flange_boundary(self):
        first = ((10, 0), (10, 50))
        perpendicular = ((0, 27), (100, 27))
        self.assertAlmostEqual(
            local_bend_dimension_mm(first, self.outline, (perpendicular,)), 10.0,
        )

    def test_local_neighbour_must_overlap_perpendicular_probe(self):
        first = ((10, 0), (10, 50))
        short_elsewhere = ((12, 70), (12, 80))
        self.assertAlmostEqual(
            local_bend_dimension_mm(first, self.outline, (short_elsewhere,)), 10.0,
        )

    def test_cad_auto_target_uses_measured_edge_and_display_height(self):
        model = BackgaugeModel2D(
            width_s_mm=10, height_z_mm=10, positions={}, automatic=True,
            edge_clearance_mm=1.0, cad_geometry_enabled=True,
        )
        bend = SimpleNamespace(
            bend_id="B01",
            bend_zone=SimpleNamespace(outer_radius_mm=2.5, thickness_mm=1.5),
        )
        position, evidence = visual_gauge_target(model, bend, 90.0)
        self.assertAlmostEqual(position.s_mm - model.width_s_mm / 2, 91.0)
        self.assertAlmostEqual(position.z_mm, 1.75)
        self.assertEqual(evidence, "VISUAL_ONLY_OUTLINE_DISTANCE")

    def test_manual_target_does_not_override_measurement(self):
        target = BackgaugePosition2D(48.0, 2.0)
        model = BackgaugeModel2D(
            width_s_mm=10, height_z_mm=10, positions={"B01": target},
            automatic=False, cad_geometry_enabled=True,
        )
        value, evidence = visual_gauge_target(
            model, SimpleNamespace(bend_id="B01"), 90.0,
        )
        self.assertIs(value, target)
        self.assertEqual(evidence, "VISUAL_ONLY_MANUAL")


if __name__ == "__main__":
    unittest.main()
