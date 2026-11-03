"""Pure-math unit tests for explicit 2.5D BendZone model and kinematics (Step 5)."""
import math
import unittest

from smbs_core.kinematics2d.bend_zone import (
    BendParameters,
    BendZone,
    BendZoneError,
    bend_parameters,
    make_bend_zone,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState


class BendZoneMathTests(unittest.TestCase):
    def test_bend_allowance_consistency(self):
        # SheetMetal angle +90 -> fold +90 deg (UP), reference radius = inner radius = 1.0
        p_up = bend_parameters(90.0, 1.0, 1.5, 0.4)
        self.assertAlmostEqual(p_up.fold_angle_deg, 90.0)
        self.assertAlmostEqual(p_up.inner_radius_mm, 1.0)
        self.assertAlmostEqual(p_up.neutral_radius_mm, 1.0 + 0.4 * 1.5)
        self.assertAlmostEqual(p_up.outer_radius_mm, 2.5)
        expected_ba = (math.pi / 2.0) * (1.0 + 0.4 * 1.5)
        self.assertAlmostEqual(p_up.bend_allowance_mm, expected_ba, places=12)

        # SheetMetal angle -90 -> fold -90 deg (DOWN), reference radius = outer radius = 2.5
        p_down = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        self.assertAlmostEqual(p_down.fold_angle_deg, -90.0)
        self.assertAlmostEqual(p_down.inner_radius_mm, 1.0)
        self.assertAlmostEqual(p_down.neutral_radius_mm, 1.6)
        self.assertAlmostEqual(p_down.outer_radius_mm, 2.5)
        self.assertAlmostEqual(p_down.bend_allowance_mm, expected_ba, places=12)

    def test_tangent_boundaries_and_frame(self):
        params = bend_parameters(90.0, 1.0, 1.5, 0.4)
        zone = make_bend_zone("B01", ((0.0, 50.0), (100.0, 50.0)), params)
        left_tan, right_tan = zone.tangent_lines()
        half_ba = params.bend_allowance_mm / 2.0
        # Axis along +X -> left_xy is +Y = (0, 1)
        self.assertAlmostEqual(zone.axis_dir_xy[0], 1.0)
        self.assertAlmostEqual(zone.axis_dir_xy[1], 0.0)
        self.assertAlmostEqual(zone.left_xy[0], 0.0)
        self.assertAlmostEqual(zone.left_xy[1], 1.0)
        self.assertAlmostEqual(left_tan[0][1], 50.0 + half_ba, places=12)
        self.assertAlmostEqual(right_tan[0][1], 50.0 - half_ba, places=12)
        self.assertAlmostEqual(zone.signed_offset((25.0, 50.0 + half_ba)), half_ba, places=12)
        self.assertAlmostEqual(zone.signed_offset((25.0, 50.0 - half_ba)), -half_ba, places=12)

    def test_intermediate_angles_preserve_neutral_length_and_distinct_surfaces(self):
        for source_ang, target_deg in (
            (135.0, 45.0),
            (-135.0, -45.0),
            (90.0, 90.0),
            (-90.0, -90.0),
            (45.0, 135.0),
        ):
            ref_r = 1.0 if source_ang > 0 else 2.5
            params = bend_parameters(source_ang, ref_r, 1.5, 0.4)
            zone = make_bend_zone("B01", ((0.0, 0.0), (100.0, 0.0)), params)
            for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
                ang = target_deg * frac
                prof = zone.section_profile(ang, segments=32)
                self.assertAlmostEqual(
                    prof["neutral_length"],
                    params.bend_allowance_mm,
                    places=12,
                    msg=f"Neutral length mismatch at target={target_deg}, frac={frac}",
                )
                # Inner, neutral, and outer surfaces must remain distinct (separated by K*t and (1-K)*t)
                for p_in, p_n, p_out in zip(prof["inner"], prof["neutral"], prof["outer"]):
                    d_in_n = math.hypot(p_in[0] - p_n[0], p_in[1] - p_n[1])
                    d_n_out = math.hypot(p_out[0] - p_n[0], p_out[1] - p_n[1])
                    d_in_out = math.hypot(p_out[0] - p_in[0], p_out[1] - p_in[1])
                    self.assertAlmostEqual(d_in_n, 0.4 * 1.5, places=9)
                    self.assertAlmostEqual(d_n_out, 0.6 * 1.5, places=9)
                    self.assertAlmostEqual(d_in_out, 1.5, places=9)

    def test_moving_transform_matches_tangent_end_for_all_required_angles(self):
        # Test 0, +45, -45, +90, -90, +135 degrees
        for source_ang, target_deg in (
            (135.0, 45.0),
            (-135.0, -45.0),
            (90.0, 90.0),
            (-90.0, -90.0),
            (45.0, 135.0),
        ):
            ref_r = 2.0 if source_ang > 0 else 3.5
            params = bend_parameters(source_ang, ref_r, 1.5, 0.4)
            zone = make_bend_zone("B01", ((0.0, 0.0), (50.0, 0.0)), params)
            half_ba = zone.half_width_mm
            _, zn = zone.section_heights()

            # At 0 deg -> identity
            t0 = zone.moving_transform(0.0, moving_left=True)
            self.assertEqual(t0.point((10.0, half_ba, zn)), (10.0, half_ba, zn))

            # At intermediate and full angles, moving tangent point on neutral layer
            # must land at the exact end of the section_profile['neutral'] polyline.
            for frac in (0.5, 1.0):
                ang = target_deg * frac
                prof = zone.section_profile(ang, segments=16)
                end_s, end_z = prof["neutral"][-1]
                for moving_left in (True, False):
                    side = 1.0 if moving_left else -1.0
                    tr = zone.moving_transform(ang, moving_left=moving_left)
                    p_folded = tr.point((10.0, side * half_ba, zn))
                    self.assertAlmostEqual(p_folded[0], 10.0, places=10)
                    self.assertAlmostEqual(p_folded[1], side * end_s, places=10)
                    self.assertAlmostEqual(p_folded[2], end_z, places=10)

    def test_sequential_and_opposite_sign_bends_in_fold_state(self):
        # P01 (y < y1) --B01(+90 UP)--> P02 (y1 < y < y2) --B02(-90 DOWN)--> P03 (y > y2)
        p_up = bend_parameters(90.0, 1.0, 1.5, 0.4)     # fold +90, inner=1.0, BA=2.513274
        p_down = bend_parameters(-90.0, 2.5, 1.5, 0.4)  # fold -90, inner=1.0, BA=2.513274
        z1 = make_bend_zone("B01", ((0.0, 10.0), (50.0, 10.0)), p_up)
        z2 = make_bend_zone("B02", ((0.0, 30.0), (50.0, 30.0)), p_down)

        # For axis (0,y)->(50,y), left_xy is (0, +1), so panel_a is the +Y side.
        g = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P01", 90.0, ((0.0, 10.0, 0.0), (50.0, 10.0, 0.0)), z1),
                GraphEdge("B02", "P03", "P02", -90.0, ((0.0, 30.0, 0.0), (50.0, 30.0, 0.0)), z2),
            ),
        )
        state = FoldState(g, "P01")
        state.set_angle("B01", 90.0)
        state.set_angle("B02", -90.0)

        # After +90 UP then -90 DOWN, P03 must be parallel to P01 again (normal +Z)
        p0 = state.transforms["P03"].point((0.0, 40.0, 0.0))
        p1 = state.transforms["P03"].point((0.0, 45.0, 0.0))
        # A step of +5 in flat Y on P03 must remain +5 in world Y and 0 in world Z
        self.assertAlmostEqual(p1[0] - p0[0], 0.0, places=10)
        self.assertAlmostEqual(p1[1] - p0[1], 5.0, places=10)
        self.assertAlmostEqual(p1[2] - p0[2], 0.0, places=10)
        # And P03 must be elevated in +Z above P01
        self.assertGreater(p0[2], 15.0)

        # Zone 3D surface polylines must be available and preserve neutral length
        sec2 = state.zone_section("B02", segments=16)
        self.assertAlmostEqual(sec2["neutral_length"], p_down.bend_allowance_mm, places=12)
        pts_neutral = state.zone_surface_points("B02", surface="neutral", segments=16)
        self.assertEqual(len(pts_neutral), 19)

    def test_no_cumulative_drift_across_repeated_angle_changes(self):
        p_up = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_down = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z1 = make_bend_zone("B01", ((0.0, 10.0), (50.0, 10.0)), p_up)
        z2 = make_bend_zone("B02", ((0.0, 30.0), (50.0, 30.0)), p_down)
        g = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P01", 90.0, ((0.0, 10.0, 0.0), (50.0, 10.0, 0.0)), z1),
                GraphEdge("B02", "P03", "P02", -90.0, ((0.0, 30.0, 0.0), (50.0, 30.0, 0.0)), z2),
            ),
        )
        state = FoldState(g, "P01")
        state.set_angle("B01", 45.0)
        state.set_angle("B02", -45.0)
        ref_point = state.transforms["P03"].point((25.0, 40.0, 0.0))

        for _ in range(200):
            for frac in (0.0, 0.1, 0.37, 0.73, 1.0, 0.5):
                state.set_fraction("B01", frac)
                state.set_fraction("B02", frac)
        after_point = state.transforms["P03"].point((25.0, 40.0, 0.0))
        for a, b in zip(ref_point, after_point):
            self.assertAlmostEqual(a, b, places=14)

        state.set_angle("B01", 0.0)
        state.set_angle("B02", 0.0)
        flat_point = state.transforms["P03"].point((25.0, 40.0, 0.0))
        self.assertEqual(flat_point, (25.0, 40.0, 0.0))

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaises(BendZoneError):
            bend_parameters(0.0, 1.0, 1.5, 0.4)
        with self.assertRaises(BendZoneError):
            bend_parameters(90.0, -1.0, 1.5, 0.4)
        with self.assertRaises(BendZoneError):
            bend_parameters(90.0, 1.0, 0.0, 0.4)
        with self.assertRaises(BendZoneError):
            bend_parameters(90.0, 1.0, 1.5, 1.5)
        with self.assertRaises(BendZoneError):
            # DOWN bend with reference (outer) radius smaller than thickness
            bend_parameters(-90.0, 1.0, 1.5, 0.4)


if __name__ == "__main__":
    unittest.main()
