"""D6.3 / D6.3.2 native geometry contract tests (pure math + optional FreeCAD)."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.bend_zone import (
    bend_parameters, make_bend_zone,
)
from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeBendSolidError, build_bend_zone_solid,
    build_single_bend_sheet_solid, build_multi_bend_sheet_solid,
    zone_outline,
)
from smbs_core.kinematics2d.native_volume_gate import (
    predict_cad_consistent_volume_mm3,
    cad_volume_consistency,
    multi_bend_cad_volume_consistency,
)


def fixture(source_angle=90.0, k_factor=0.4, thickness=1.5, length=100.0):
    reference_radius = 1.0 if source_angle > 0 else 1.0 + thickness
    params = bend_parameters(source_angle, reference_radius, thickness, k_factor)
    z = make_bend_zone("B01", ((0., 0.), (length, 0.)), params)
    graph = PanelGraph(("P01", "P02"), (
        GraphEdge("B01", "P01", "P02", params.fold_angle_deg,
                  ((0., 0., 0.), (length, 0., 0.)), z),
    ))
    return FoldState(graph, "P01")


def multi_fixture_z():
    """Two-bend Z-bracket topology with 3 panels: P01 - B02 - P02 - B01 - P03."""
    thickness = 1.5
    p1 = bend_parameters(90.0, 1.0, thickness, 0.4)
    z1 = make_bend_zone("B01", ((100., 50.), (0., 50.)), p1)
    p2 = bend_parameters(-90.0, 2.5, thickness, 0.4)
    z2 = make_bend_zone("B02", ((0., 10.), (100., 10.)), p2)
    graph = PanelGraph(("P01", "P02", "P03"), (
        GraphEdge("B01", "P02", "P03", p1.fold_angle_deg, ((100., 50., 0.), (0., 50., 0.)), z1),
        GraphEdge("B02", "P02", "P01", p2.fold_angle_deg, ((0., 10., 0.), (100., 10., 0.)), z2),
    ))
    return FoldState(graph, "P02")


def planar_area(points):
    return abs(sum(p[0]*q[1]-q[0]*p[1]
                   for p, q in zip(points, points[1:]+points[:1])) / 2)


class ZoneOutlineTests(unittest.TestCase):
    def test_flat_volume_section_area(self):
        for signed in (90., -90., 135., -135.):
            state = fixture(signed)
            z = state.bends["B01"].bend_zone
            outline = zone_outline(z, 0.)
            self.assertAlmostEqual(
                planar_area(outline),
                z.bend_allowance_mm * z.thickness_mm, places=6,
            )

    def test_all_angles_are_positive_area(self):
        for source in (90., -90., 135., -135., 45.):
            state = fixture(source)
            z = state.bends["B01"].bend_zone
            for fraction in (0., 0.25, 0.5, 0.75, 1.):
                points = zone_outline(z, z.fold_angle_deg * fraction)
                self.assertGreater(planar_area(points), 0.01)
                self.assertTrue(all(math.isfinite(x) and math.isfinite(y) for x, y in points))

    def test_no_adjacent_duplicates(self):
        z = fixture().bends["B01"].bend_zone
        for a in (0., 45., 90.):
            pts = zone_outline(z, a)
            self.assertTrue(all(math.dist(p, q) > 1e-8 for p, q in zip(pts, pts[1:]+pts[:1])))

    def test_bad_sampling_fails(self):
        with self.assertRaises(NativeBendSolidError):
            zone_outline(fixture().bends["B01"].bend_zone, 0, segments=4)

    def test_missing_zone_fails_closed(self):
        z = fixture()
        with self.assertRaises(NativeBendSolidError):
            build_bend_zone_solid(z, "NOT_A_BEND")

    def test_complete_sheet_requires_actual_panel_faces(self):
        state = fixture()
        with self.assertRaises(NativeBendSolidError):
            build_single_bend_sheet_solid(state, None, "B01")

    def test_single_bend_sheet_rejects_multi_bend(self):
        # build_single_bend_sheet_solid strictly requires 1 bend and 2 panels
        state = multi_fixture_z()
        with self.assertRaises(NativeBendSolidError):
            build_single_bend_sheet_solid(state, None, "B01")

    def test_multi_bend_sheet_fails_on_missing_or_mismatched_panels(self):
        state = multi_fixture_z()
        with self.assertRaises(NativeBendSolidError):
            build_multi_bend_sheet_solid(state, None)

        # Mismatched panel count
        bad_pb = SimpleNamespace(
            panels=[SimpleNamespace(panel_id="P01")],
            panel_faces=[None],
        )
        with self.assertRaises(NativeBendSolidError):
            build_multi_bend_sheet_solid(state, bad_pb)

    def test_synthetic_cad_volume_across_k_factors_and_signs(self):
        for source in (90.0, -90.0, 45.0, -45.0):
            for k in (0.3, 0.4, 0.5):
                state = fixture(source, k_factor=k, thickness=2.0, length=150.0)
                z = state.bends["B01"].bend_zone
                angles = [z.fold_angle_deg * f for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
                poly_volumes = []
                for a in angles:
                    outline = zone_outline(z, a, segments=64)
                    area = planar_area(outline)
                    poly_volumes.append(area * 150.0)

                res = cad_volume_consistency(
                    poly_volumes,
                    angles_deg=angles,
                    thickness_mm=z.thickness_mm,
                    k_factor=z.k_factor_ansi,
                    bend_length_mm=150.0,
                    inner_radius_mm=z.inner_radius_mm,
                    segments=64,
                )
                self.assertTrue(res["pass"])
                self.assertLess(res["maximum_absolute_residual_mm3"], 1e-4)

    def test_live_freecad_part_if_available(self):
        try:
            import FreeCAD
            import Part
        except ImportError:
            self.skipTest("native FreeCAD Part runtime is required")
        for source in (90., -90., 135., -135.):
            state = fixture(source)
            z = state.bends["B01"].bend_zone
            sampled_volumes = []
            sampled_angles = []
            for fraction in (0., 0.25, 0.5, 0.75, 1.):
                state.set_fraction("B01", fraction)
                result = build_bend_zone_solid(state, "B01", segments=64)
                self.assertTrue(result.shape.isValid())
                self.assertTrue(result.shape.isClosed())
                self.assertEqual(len(result.shape.Solids), 1)
                self.assertGreater(result.volume_mm3, 0)
                self.assertAlmostEqual(
                    result.volume_mm3, result.section_area_mm2 * result.bend_line_length_mm,
                    places=4,
                )
                self.assertFalse(result.physical_certified)
                sampled_volumes.append(result.volume_mm3)
                sampled_angles.append(state.angles["B01"])

            gate = cad_volume_consistency(
                sampled_volumes,
                angles_deg=sampled_angles,
                thickness_mm=z.thickness_mm,
                k_factor=z.k_factor_ansi,
                bend_length_mm=100.0,
                inner_radius_mm=z.inner_radius_mm,
                segments=64,
            )
            self.assertTrue(gate["pass"])
            self.assertLess(gate["maximum_absolute_residual_mm3"], 1e-3)


if __name__ == "__main__":
    unittest.main()
