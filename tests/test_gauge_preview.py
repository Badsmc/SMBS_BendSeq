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
    gauge_rear_envelope_from_section,
    sequenced_gauge_envelopes,
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

    def test_machine_envelope_includes_thickness_and_signed_slope(self):
        # A physical sheet section with a previously folded 1.5-mm wall.
        section = {
            "fixed_polygon": ((0, 0), (22.0, 0), (22.0, 1.5), (0, 1.5)),
            "moving_polygon": ((-5.0, 0), (-5.0, 1.5)),
            "zone_polygon": (),
            "other_panel_polygons": [
                ("P01", ((21.99, 0), (23.5, 0.5), (23.5, 2.0),
                         (21.99, 1.5))),
            ],
            "other_zone_polygons": [],
        }
        self.assertEqual(gauge_rear_envelope_from_section(section), (23.5, 1.25))

        # A 45-degree return wall exposes a different rear-most profile:
        # never use a universal +1.5-mm fudge factor.
        forty_five = {
            **section,
            "other_panel_polygons": [
                ("P01", ((22.0, 0), (23.06066, 1.06066),
                         (22.0, 2.12132))),
            ],
        }
        s, z = gauge_rear_envelope_from_section(forty_five)
        self.assertAlmostEqual(s, 23.06066, places=5)
        self.assertAlmostEqual(z, 1.06066, places=5)

    def test_cad_auto_target_uses_folded_envelope_s_and_z(self):
        model = BackgaugeModel2D(
            width_s_mm=10, height_z_mm=10, positions={}, automatic=True,
            edge_clearance_mm=1.0, cad_geometry_enabled=True,
        )
        bend = SimpleNamespace(bend_id="B01")
        pos, evidence = visual_gauge_target(model, bend, 23.5, 2.0)
        self.assertAlmostEqual(pos.s_mm - model.width_s_mm / 2, 24.5)
        self.assertAlmostEqual(pos.z_mm, 2.0)
        self.assertEqual(evidence, "VISUAL_ONLY_FOLDED_MACHINE_ENVELOPE")
        with self.assertRaisesRegex(ValueError, "missing physical"):
            visual_gauge_target(model, bend, 22.0, None)

    def test_manual_target_overrides_automatic_rear_envelope(self):
        target = BackgaugePosition2D(48.0, 2.0)
        model = BackgaugeModel2D(
            width_s_mm=10, height_z_mm=10, positions={"B01": target},
            automatic=False, cad_geometry_enabled=True,
        )
        value, evidence = visual_gauge_target(
            model, SimpleNamespace(bend_id="B01"), None, None,
        )
        self.assertIs(value, target)
        self.assertEqual(evidence, "VISUAL_ONLY_MANUAL")

    def test_sequential_gauge_uses_authoritative_signed_fold_state(self):
        from smbs_core.kinematics2d.bend_zone import (
            make_bend_zone, bend_parameters,
        )
        from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
        from smbs_core.kinematics2d.panel_kinematics import FoldState

        t, radius, k = 1.5, 1.0, 0.31
        a = ((10, 0), (10, 50))
        b = ((32, 0), (32, 50))
        def zone(bid, axis, angle):
            source_angle = math.copysign(180 - abs(angle), angle)
            reference_radius = radius if angle > 0 else radius + t
            return make_bend_zone(
                bid, axis,
                bend_parameters(source_angle, reference_radius, t, k),
            )
        bz1, bz2 = zone("B01", a, 90), zone("B02", b, -45)
        graph = PanelGraph(("P01", "P02", "P03"), (
            GraphEdge("B01", "P01", "P02", 90.0,
                      ((10, 0, 0), (10, 50, 0)), bz1),
            GraphEdge("B02", "P02", "P03", -45.0,
                      ((32, 0, 0), (32, 50, 0)), bz2),
        ))
        def rect(lo, hi):
            return ((lo, 0), (hi, 0), (hi, 50), (lo, 50))
        panels = {
            "P01": rect(0, 10 - bz1.half_width_mm),
            "P02": rect(10 + bz1.half_width_mm, 32 - bz2.half_width_mm),
            "P03": rect(32 + bz2.half_width_mm, 100),
        }
        state = FoldState(graph, "P01")
        session = SimpleNamespace(
            bend_order=("B01", "B02"), graph=graph, fixed_panel="P01",
            panel_vertices_xy=panels, fold_state=state,
        )
        targets, errors = sequenced_gauge_envelopes(session)
        self.assertEqual(errors, {}, errors)
        self.assertEqual(set(targets), {"B01", "B02"})
        self.assertAlmostEqual(targets["B01"][0], 10.0, delta=0.5)
        self.assertNotAlmostEqual(targets["B02"][0], 22.0, delta=0.05)
        # Source FoldState is NOT mutated by the per-step setup computation.
        self.assertEqual(state.angles, {"B01": 0.0, "B02": 0.0})

        # Order is physically important: swapping operations changes the
        # antecedent geometry, despite identical flat-pattern dimensions.
        reverse = SimpleNamespace(**{
            **session.__dict__, "bend_order": ("B02", "B01"),
        })
        reverse_targets, reverse_errors = sequenced_gauge_envelopes(reverse)
        self.assertEqual(reverse_errors, {}, reverse_errors)
        self.assertNotAlmostEqual(
            targets["B02"][0], reverse_targets["B02"][0], delta=0.05,
        )
        # Paired +45/-45 folds use actual signed finite-radius kinematics,
        # not just flat panel lengths or a universal +thickness correction.
        signed_results = {}
        for a1, a2 in ((45, -45), (45, 45), (-45, 45)):
            first, second = zone("B01", a, a1), zone("B02", b, a2)
            signed_graph = PanelGraph(("P01", "P02", "P03"), (
                GraphEdge("B01", "P01", "P02", float(a1),
                          ((10, 0, 0), (10, 50, 0)), first),
                GraphEdge("B02", "P02", "P03", float(a2),
                          ((32, 0, 0), (32, 50, 0)), second),
            ))
            signed_panels = {
                "P01": rect(0, 10 - first.half_width_mm),
                "P02": rect(10 + first.half_width_mm, 32 - second.half_width_mm),
                "P03": rect(32 + second.half_width_mm, 100),
            }
            signed_state = FoldState(signed_graph, "P01")
            signed_session = SimpleNamespace(
                bend_order=("B01", "B02"), graph=signed_graph,
                fixed_panel="P01", panel_vertices_xy=signed_panels,
                fold_state=signed_state,
            )
            signed_targets, signed_errors = sequenced_gauge_envelopes(
                signed_session,
            )
            self.assertEqual(signed_errors, {}, (a1, a2, signed_errors))
            self.assertIn("B02", signed_targets)
            signed_results[(a1, a2)] = signed_targets["B02"][0]
        print("MACHINE_GAUGE_PROJECTIONS_MM:", {
            "B01_90_then_B02_minus45": round(targets["B02"][0], 5),
            **{
                "B01_{}_B02_{}".format(a1, a2): round(value, 5)
                for (a1, a2), value in signed_results.items()
            },
        })

    def test_opposite_45_pair_changes_third_bend_machine_reach(self):
        from smbs_core.kinematics2d.bend_zone import (
            make_bend_zone, bend_parameters,
        )
        from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
        from smbs_core.kinematics2d.panel_kinematics import FoldState

        def make_fixture(second_angle):
            t, radius, k = 1.5, 1.0, 0.31
            bend_data = (("B01", 10, +45), ("B02", 32, second_angle),
                         ("B03", 54, +90))
            zones = {}
            for bid, x, angle in bend_data:
                reference_radius = radius if angle > 0 else radius + t
                params = bend_parameters(
                    math.copysign(180 - abs(angle), angle),
                    reference_radius, t, k,
                )
                zones[bid] = make_bend_zone(
                    bid, ((x, 0), (x, 50)), params,
                )
            names = ("P01", "P02", "P03", "P04")
            edges = tuple(
                GraphEdge(bid, names[i], names[i + 1], angle,
                          ((x, 0, 0), (x, 50, 0)), zones[bid])
                for i, (bid, x, angle) in enumerate(bend_data)
            )
            graph = PanelGraph(names, edges)
            def rect(lo, hi):
                return ((lo, 0), (hi, 0), (hi, 50), (lo, 50))
            vertices = {
                "P01": rect(0, 10 - zones["B01"].half_width_mm),
                "P02": rect(10 + zones["B01"].half_width_mm,
                            32 - zones["B02"].half_width_mm),
                "P03": rect(32 + zones["B02"].half_width_mm,
                            54 - zones["B03"].half_width_mm),
                "P04": rect(54 + zones["B03"].half_width_mm, 100),
            }
            return SimpleNamespace(
                bend_order=("B01", "B02", "B03"), graph=graph,
                fixed_panel="P01", fold_state=FoldState(graph, "P01"),
                panel_vertices_xy=vertices,
            )

        mixed, mixed_errors = sequenced_gauge_envelopes(
            make_fixture(-45),
        )
        same, same_errors = sequenced_gauge_envelopes(
            make_fixture(+45),
        )
        self.assertEqual(mixed_errors, {}, mixed_errors)
        self.assertEqual(same_errors, {}, same_errors)
        self.assertAlmostEqual(
            mixed["B01"][0], same["B01"][0], places=5,
        )
        self.assertAlmostEqual(
            mixed["B02"][0], same["B02"][0], places=5,
        )
        # Only *after* the second bend has happened does its sign change
        # the geometry that must be projected to set up the next operation.
        print("AFTER_B01_45_B02_MINUS45_B03_REACH", mixed["B03"][0])
        print("AFTER_B01_45_B02_PLUS45_B03_REACH", same["B03"][0])
        self.assertGreater(
            abs(mixed["B03"][0] - same["B03"][0]), 0.25,
        )


if __name__ == "__main__":
    unittest.main()
