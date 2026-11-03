"""Pure-math unit tests for explicit 2.5D BendZone model and kinematics (Step 5)."""
import math
import unittest

from freecad_smbs.commands.pressbrake_2d import (
    LoadedPartSession,
    SelectionWarning,
    execute_load_part,
    resolve_selected_unfold,
    validate_selection_preconditions,
)
from freecad_smbs.InitGui import COMMANDS_MAP, register_commands
from smbs_core.kinematics2d.bend_zone import (
    BendParameters,
    BendZone,
    BendZoneError,
    bend_parameters,
    make_bend_zone,
)
from smbs_core.kinematics2d.fold_sequence import (
    BendSequenceController,
    PlaybackStatus,
    SequenceError,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState, KinematicsError


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

    def test_opposite_angle_direction_rejected_and_fold_state_is_atomic(self):
        p_up = bend_parameters(90.0, 1.0, 1.5, 0.4)     # +90 deg
        p_down = bend_parameters(-90.0, 2.5, 1.5, 0.4)  # -90 deg
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
        state.set_angle("B02", -30.0)
        angles_before = dict(state.angles)
        transforms_before = dict(state.transforms)

        # Opposite direction on +90 bend (-10 deg) must fail and leave state untouched
        with self.assertRaises(KinematicsError):
            state.set_angle("B01", -10.0)
        self.assertEqual(state.angles, angles_before)
        self.assertEqual(state.transforms, transforms_before)

        # Opposite direction on -90 bend (+10 deg) must fail and leave state untouched
        with self.assertRaises(KinematicsError):
            state.set_angle("B02", 10.0)
        self.assertEqual(state.angles, angles_before)
        self.assertEqual(state.transforms, transforms_before)

        # Exceeding target angle magnitude must fail and leave state untouched
        with self.assertRaises(KinematicsError):
            state.set_angle("B01", 95.0)
        self.assertEqual(state.angles, angles_before)
        self.assertEqual(state.transforms, transforms_before)

    def test_cross_section_2d_for_l_plus90_minus90_and_45_deg(self):
        # Verify L-part +90, L-part -90, and 45 deg bend cross-sections and 0 -> 45 -> 90 -> 0 cycle
        for source_ang, target_deg, ref_r in ((90.0, 90.0, 1.0), (-90.0, -90.0, 2.5), (135.0, 45.0, 1.0)):
            params = bend_parameters(source_ang, ref_r, 1.5, 0.4)
            half_ba = params.bend_allowance_mm / 2.0
            y_axis = 47.5 + half_ba
            zone = make_bend_zone("B01", ((0.0, y_axis), (200.0, y_axis)), params)
            # P02 on +Y side [y_axis + half_ba, y_axis + half_ba + 47.5], P01 on -Y side [0, 47.5]
            g = PanelGraph(
                ("P01", "P02"),
                (GraphEdge("B01", "P02", "P01", params.fold_angle_deg, ((0.0, y_axis, 0.0), (200.0, y_axis, 0.0)), zone),),
            )
            verts = {
                "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 47.5), (0.0, 47.5)),
                "P02": ((0.0, y_axis + half_ba), (200.0, y_axis + half_ba), (200.0, y_axis + half_ba + 47.5), (0.0, y_axis + half_ba + 47.5)),
            }
            session = LoadedPartSession(
                part_label="L_test",
                unfold_data=None,
                panel_build=None,
                graph=g,
                panel_vertices_xy=verts,
                fixed_panel="P02",
                fold_state=FoldState(g, "P02"),
            )

            # Cycle 0 -> half -> full -> 0
            for frac in (0.0, 0.5, 1.0, 0.0):
                ang = target_deg * frac
                session.set_active_angle(ang)
                sec = session.cross_section(segments=24)
                self.assertAlmostEqual(sec["fixed_length_mm"], 47.5, places=9)
                self.assertAlmostEqual(sec["moving_length_mm"], 47.5, places=9)
                self.assertAlmostEqual(sec["angle_deg"], ang, places=9)

                # Continuity at tangent boundaries between rigid panels and bend zone
                top_zone = sec["inner_surface"][1:-1] if target_deg > 0 else sec["outer_surface"][1:-1]
                bot_zone = sec["outer_surface"][1:-1] if target_deg > 0 else sec["inner_surface"][1:-1]
                t_fixed_top, t_moving_top = sec["tangent_points_top"]
                t_fixed_bot, t_moving_bot = sec["tangent_points_bottom"]
                for a, b in ((t_fixed_top, top_zone[0]), (t_moving_top, top_zone[-1]),
                             (t_fixed_bot, bot_zone[0]), (t_moving_bot, bot_zone[-1])):
                    self.assertAlmostEqual(a[0], b[0], places=10)
                    self.assertAlmostEqual(a[1], b[1], places=10)

            # For +/-90 deg L-bracket with 47.5 mm straight legs, R_in=1.0, t=1.5 (R_out=2.5),
            # the folded 90-deg outer bounding box must be exactly 50.0 mm x 50.0 mm.
            if abs(target_deg) == 90.0:
                session.set_active_angle(target_deg)
                sec90 = session.cross_section(segments=32)
                all_pts = sec90["inner_surface"] + sec90["outer_surface"]
                s_vals = [p[0] for p in all_pts]
                z_vals = [p[1] for p in all_pts]
                self.assertAlmostEqual(max(s_vals) - min(s_vals), 50.0, places=9)
                self.assertAlmostEqual(max(z_vals) - min(z_vals), 50.0, places=9)
                if target_deg > 0:
                    self.assertGreater(max(z_vals), 45.0)
                else:
                    self.assertLess(min(z_vals), -45.0)

    def test_multi_bend_z90_and_135_cross_sections_match_3d_reference_slices(self):
        # 1) Z-(ok).FCStd reference geometry: P02 middle web (45 mm), P03 (+90 UP, 12.5 mm), P01 (-90 DOWN, 12.5 mm)
        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 61.26991118430775), (0.0, 61.26991118430775)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 13.756637061435917), (200.0, 13.756637061435917)), p_dn90)
        g_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 61.26991118430775, 0.0), (0.0, 61.26991118430775, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 13.756637061435917, 0.0), (200.0, 13.756637061435917, 0.0)), z_b02),
            ),
        )
        verts_z = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 12.5), (0.0, 12.5)),
            "P02": ((0.0, 15.013274122871834), (200.0, 15.013274122871834), (200.0, 60.013274122871834), (0.0, 60.013274122871834)),
            "P03": ((0.0, 62.52654824574367), (200.0, 62.52654824574367), (200.0, 75.02654824574367), (0.0, 75.02654824574367)),
        }
        state_z = FoldState(g_z, "P02")
        sess_z = LoadedPartSession("Z-(ok)", None, None, g_z, verts_z, "P02", state_z, 0)
        # Initial state must have ALL bends at 0 deg (flat sheet)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})
        self.assertEqual(sess_z.active_bend_id, "B01")
        self.assertAlmostEqual(sess_z.current_angle_deg, 0.0, places=9)

        # Step 1: Bend B01 smoothly 0 -> +45 -> +90 while B02 remains at 0 deg
        sess_z.set_active_angle(45.0)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 45.0, "B02": 0.0})
        sess_z.set_active_angle(90.0)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": 0.0})

        # Step 2: Switch to B02 -> B01 must keep +90 deg and B02 must start at 0 deg
        sess_z.select_bend_index(1)
        self.assertEqual(sess_z.active_bend_id, "B02")
        self.assertAlmostEqual(sess_z.current_angle_deg, 0.0, places=9)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": 0.0})

        # Bend B02 smoothly 0 -> -45 -> -90 while B01 stays at +90 deg
        sess_z.set_active_angle(-45.0)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": -45.0})
        sess_z.set_active_angle(-90.0)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})

        # Switch back to B01 to inspect cross-section in B01 frame -> both bends remain at target
        sess_z.select_bend_index(0)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})
        sec_z = sess_z.cross_section(segments=32)

        # Convert active B01 (s, z) back to SketchFrame (Y, Z): Y = 61.26991118430775 + s
        oy_z = 61.26991118430775
        all_z_yz = (
            [(oy_z + p[0], p[1]) for p in sec_z["fixed_polygon"]]
            + [(oy_z + p[0], p[1]) for p in sec_z["moving_polygon"]]
            + [(oy_z + p[0], p[1]) for p in sec_z["other_panel_polygons"][0][1]]
        )
        #Exact 12 reference vertices sliced from Z-(ok).FCStd Body.Shape in SketchFrame:
        expected_z_yz = [
            (12.513274122871834, -15.0),
            (12.513274122871834, -2.5),
            (14.013274122871834, -15.0),
            (14.013274122871834, -2.5),
            (15.013274122871834, -1.5),
            (15.013274122871834, 0.0),
            (60.013274122871834, -1.5),
            (60.013274122871834, 0.0),
            (61.013274122871834, 1.0),
            (61.013274122871834, 13.5),
            (62.513274122871834, 1.0),
            (62.513274122871834, 13.5),
        ]
        for ref_y, ref_z in expected_z_yz:
            min_dist = min(math.hypot(y - ref_y, z - ref_z) for y, z in all_z_yz)
            self.assertLess(min_dist, 1e-9, f"Z-(ok) reference vertex ({ref_y}, {ref_z}) missed by {min_dist}")

        # Reset must return all bends to 0 deg (flat pattern)
        sess_z.reset_all_bends()
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # 2) 135-(ok).FCStd reference geometry: P02 middle web (45 mm), P03 (+45 UP, 13.964466 mm), P01 (-45 DOWN, 13.964466 mm)
        p_up45 = bend_parameters(135.0, 1.0, 1.5, 0.4)
        p_dn45 = bend_parameters(-135.0, 2.5, 1.5, 0.4)
        z_135_1 = make_bend_zone("B01", ((200.0, 60.849421686221135), (0.0, 60.849421686221135)), p_up45)
        z_135_2 = make_bend_zone("B02", ((0.0, 14.59278462478522), (200.0, 14.59278462478522)), p_dn45)
        g_135 = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 45.0, ((200.0, 60.849421686221135, 0.0), (0.0, 60.849421686221135, 0.0)), z_135_1),
                GraphEdge("B02", "P02", "P01", -45.0, ((0.0, 14.59278462478522, 0.0), (200.0, 14.59278462478522, 0.0)), z_135_2),
            ),
        )
        verts_135 = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 13.964466094067262), (0.0, 13.964466094067262)),
            "P02": ((0.0, 15.22110315550318), (200.0, 15.22110315550318), (200.0, 60.22110315550318), (0.0, 60.22110315550318)),
            "P03": ((0.0, 61.477740216939095), (200.0, 61.477740216939095), (200.0, 75.44220631100636), (0.0, 75.44220631100636)),
        }
        state_135 = FoldState(g_135, "P02")
        sess_135 = LoadedPartSession("135-(ok)", None, None, g_135, verts_135, "P02", state_135, 0)
        # Initial state: all bends at 0 deg
        self.assertEqual(sess_135.fold_state.angles, {"B01": 0.0, "B02": 0.0})
        # Bend B01: 0 -> +45 deg, B02 stays 0 deg
        sess_135.set_active_angle(45.0)
        self.assertEqual(sess_135.fold_state.angles, {"B01": 45.0, "B02": 0.0})
        # Switch to B02: B01 stays +45 deg, B02 starts at 0 deg -> bend to -45 deg
        sess_135.select_bend_index(1)
        self.assertAlmostEqual(sess_135.current_angle_deg, 0.0, places=9)
        self.assertEqual(sess_135.fold_state.angles, {"B01": 45.0, "B02": 0.0})
        sess_135.set_active_angle(-45.0)
        self.assertEqual(sess_135.fold_state.angles, {"B01": 45.0, "B02": -45.0})
        sec_135 = sess_135.cross_section(segments=32)
        all_135_pts = (
            list(sec_135["fixed_polygon"])
            + list(sec_135["moving_polygon"])
            + list(sec_135["other_panel_polygons"][0][1])
        )
        s_135 = [p[0] for p in all_135_pts]
        z_135 = [p[1] for p in all_135_pts]
        # Total Y span of 135-(ok).FCStd Body.Shape in SketchFrame is 59.1421356 - (-9.1421356) = 68.2842712 mm
        # Total Z span is 10.1672619 - (-11.6672619) = 21.8345238 mm
        self.assertAlmostEqual(max(s_135) - min(s_135), 68.2842712474619, places=9)
        self.assertAlmostEqual(max(z_135) - min(z_135), 21.83452377915607, places=9)

        # Reset returns all bends of 135-(ok) to 0 deg
        sess_135.reset_all_bends()
        self.assertEqual(sess_135.fold_state.angles, {"B01": 0.0, "B02": 0.0})

    def test_missing_selection_fails_with_clear_error_and_initgui_no_duplicate_registration(self):
        class _Doc:
            Objects = ()
        with self.assertRaises(SelectionWarning):
            validate_selection_preconditions(None, [])
        with self.assertRaises(SelectionWarning):
            validate_selection_preconditions(_Doc(), [])
        with self.assertRaises(SelectionWarning):
            resolve_selected_unfold(None, [])
        with self.assertRaises(SelectionWarning):
            resolve_selected_unfold(_Doc(), [])

        import freecad_smbs.workbench_runtime as _rt
        from unittest.mock import MagicMock, patch
        fake_gui = MagicMock()
        fake_gui.listCommands.return_value = []
        with patch.object(_rt, "FreeCADGui", fake_gui), \
             patch.object(_rt, "_REGISTERED_COMMANDS", set()):
            register_commands()
            first_count = fake_gui.addCommand.call_count
            self.assertEqual(first_count, len(COMMANDS_MAP))
            register_commands()
            self.assertEqual(fake_gui.addCommand.call_count, first_count)

    def test_empty_selection_and_missing_doc_preserve_previous_session_without_pipeline_calls(self):
        params = bend_parameters(90.0, 1.0, 1.5, 0.4)
        half_ba = params.bend_allowance_mm / 2.0
        y_axis = 47.5 + half_ba
        zone = make_bend_zone("B01", ((0.0, y_axis), (200.0, y_axis)), params)
        g = PanelGraph(
            ("P01", "P02"),
            (GraphEdge("B01", "P02", "P01", params.fold_angle_deg, ((0.0, y_axis, 0.0), (200.0, y_axis, 0.0)), zone),),
        )
        verts = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 47.5), (0.0, 47.5)),
            "P02": ((0.0, y_axis + half_ba), (200.0, y_axis + half_ba), (200.0, y_axis + half_ba + 47.5), (0.0, y_axis + half_ba + 47.5)),
        }
        existing_session = LoadedPartSession(
            part_label="Existing_L_Part",
            unfold_data=None,
            panel_build=None,
            graph=g,
            panel_vertices_xy=verts,
            fixed_panel="P02",
            fold_state=FoldState(g, "P02"),
        )
        existing_session.set_active_angle(45.0)

        class _Doc:
            Objects = ()

        pipeline_calls = []

        def _forbidden_build(doc, sel):
            pipeline_calls.append((doc, sel))
            raise AssertionError("build_part_session must not be called on empty selection or missing doc")

        # 1) Empty selection with active document: warning returned, pipeline NOT called,
        # FreeCAD.Console NOT called, session & angle preserved
        from unittest.mock import MagicMock, patch
        import sys
        import freecad_smbs.commands.pressbrake_2d as pb2d

        fake_freecad = MagicMock()
        fake_gui = MagicMock()
        fake_gui.Selection.getSelection.return_value = []
        fake_freecad.ActiveDocument = _Doc()

        with patch.dict(sys.modules, {"FreeCAD": fake_freecad, "FreeCADGui": fake_gui}), \
             patch.object(pb2d, "_log") as mock_log, \
             patch.object(pb2d, "_warn") as mock_warn, \
             patch.object(pb2d, "_error") as mock_err:
            outcome1, sess1, msg1 = execute_load_part(existing_session, _Doc(), [], build_fn=_forbidden_build)
            self.assertEqual(outcome1, "warning")
            self.assertIs(sess1, existing_session)
            self.assertAlmostEqual(sess1.current_angle_deg, 45.0, places=9)
            self.assertEqual(len(pipeline_calls), 0)
            self.assertIn("Деталь не выбрана", msg1)
            mock_log.assert_not_called()
            mock_warn.assert_not_called()
            mock_err.assert_not_called()
            fake_freecad.Console.PrintMessage.assert_not_called()
            fake_freecad.Console.PrintWarning.assert_not_called()
            fake_freecad.Console.PrintError.assert_not_called()

            # 2) Missing document: warning returned, pipeline NOT called, FreeCAD.Console NOT called
            outcome2, sess2, msg2 = execute_load_part(existing_session, None, ["dummy"], build_fn=_forbidden_build)
            self.assertEqual(outcome2, "warning")
            self.assertIs(sess2, existing_session)
            self.assertAlmostEqual(sess2.current_angle_deg, 45.0, places=9)
            self.assertEqual(len(pipeline_calls), 0)
            self.assertIn("Нет открытого документа", msg2)
            mock_log.assert_not_called()
            mock_warn.assert_not_called()
            mock_err.assert_not_called()
            fake_freecad.Console.PrintMessage.assert_not_called()
            fake_freecad.Console.PrintWarning.assert_not_called()
            fake_freecad.Console.PrintError.assert_not_called()

        # Verify that even when _log / _warn / _error are called, they NEVER invoke FreeCAD.Console
        with patch.dict(sys.modules, {"FreeCAD": fake_freecad, "FreeCADGui": fake_gui}):
            pb2d._log("diagnostic test")
            pb2d._warn("warning test")
            pb2d._error("error test")
            fake_freecad.Console.PrintMessage.assert_not_called()
            fake_freecad.Console.PrintWarning.assert_not_called()
            fake_freecad.Console.PrintError.assert_not_called()

        # 3) Unexpected error during build: error returned, previous session STILL preserved
        def _failing_build(doc, sel):
            pipeline_calls.append((doc, sel))
            raise ValueError("Simulated corrupt geometry")

        outcome3, sess3, msg3 = execute_load_part(existing_session, _Doc(), ["Body"], build_fn=_failing_build)
        self.assertEqual(outcome3, "error")
        self.assertIs(sess3, existing_session)
        self.assertAlmostEqual(sess3.current_angle_deg, 45.0, places=9)
        self.assertEqual(len(pipeline_calls), 1)
        self.assertIn("Simulated corrupt geometry", msg3)

        # 4) Subsequent valid selection after warning/error loads the new session cleanly
        new_session = LoadedPartSession(
            part_label="New_Loaded_Part",
            unfold_data=None,
            panel_build=None,
            graph=g,
            panel_vertices_xy=verts,
            fixed_panel="P02",
            fold_state=FoldState(g, "P02"),
        )
        outcome4, sess4, msg4 = execute_load_part(
            existing_session, _Doc(), ["Body"], build_fn=lambda d, s: new_session
        )
        self.assertEqual(outcome4, "loaded")
        self.assertIs(sess4, new_session)
        self.assertIn("New_Loaded_Part", msg4)

        # 5) Verify Panel.load_part() does NOT touch timer, scene, draw, fit, or operations on warning
        mock_qt_core = MagicMock()
        mock_qt_gui = MagicMock()
        mock_qt_widgets = MagicMock()

        class _DummyWidget:
            ScrollHandDrag = 1
            def __init__(self, *args, **kwargs):
                pass
            def setMinimumWidth(self, *args):
                pass
            def setMinimumHeight(self, *args):
                pass
            def setRenderHint(self, *args):
                pass
            def setDragMode(self, *args):
                pass
            def setHorizontalScrollBarPolicy(self, *args):
                pass
            def setVerticalScrollBarPolicy(self, *args):
                pass
            def setScene(self, *args):
                pass

        mock_qt_widgets.QWidget = _DummyWidget
        mock_qt_widgets.QGraphicsView = _DummyWidget

        with patch.object(pb2d, "_qt", return_value=(mock_qt_core, mock_qt_gui, mock_qt_widgets)), \
             patch.dict(sys.modules, {"FreeCAD": fake_freecad, "FreeCADGui": fake_gui}):
            panel = pb2d._PreviewPanel()
            panel.session = existing_session
            panel.angle = 45.0
            panel.playing = True
            panel.timer = MagicMock()
            panel.scene = MagicMock()
            panel.operations = MagicMock()
            panel.draw = MagicMock()
            panel.fit = MagicMock()
            panel._populate_operations = MagicMock()
            panel._configure_for_active_bend = MagicMock()
            panel._set_playback_controls_enabled = MagicMock()
            panel.status = MagicMock()
            panel.check_result = MagicMock()

            # Simulate 5 consecutive clicks on "Деталь" with empty selection
            for _ in range(5):
                panel.load_part()

            self.assertIs(panel.session, existing_session)
            self.assertEqual(panel.angle, 45.0)
            self.assertTrue(panel.playing)
            panel.timer.stop.assert_not_called()
            panel.scene.clear.assert_not_called()
            panel.operations.clear.assert_not_called()
            panel.draw.assert_not_called()
            panel.fit.assert_not_called()
            panel._populate_operations.assert_not_called()
            panel._configure_for_active_bend.assert_not_called()
            panel._set_playback_controls_enabled.assert_not_called()
            panel.check_result.setText.assert_not_called()
            self.assertEqual(panel.status.setText.call_count, 5)
            fake_freecad.Console.PrintMessage.assert_not_called()
            fake_freecad.Console.PrintWarning.assert_not_called()
            fake_freecad.Console.PrintError.assert_not_called()

    def test_panel_sequential_bending_and_reset_for_l_z_and_135(self):
        from unittest.mock import MagicMock, patch
        import freecad_smbs.commands.pressbrake_2d as pb2d

        mock_qt_core = MagicMock()
        mock_qt_gui = MagicMock()
        mock_qt_widgets = MagicMock()

        class _DummyWidget:
            ScrollHandDrag = 1
            def __init__(self, *args, **kwargs):
                pass
            def setMinimumWidth(self, *args):
                pass
            def setMinimumHeight(self, *args):
                pass
            def setRenderHint(self, *args):
                pass
            def setDragMode(self, *args):
                pass
            def setHorizontalScrollBarPolicy(self, *args):
                pass
            def setVerticalScrollBarPolicy(self, *args):
                pass
            def setScene(self, *args):
                pass

        mock_qt_widgets.QWidget = _DummyWidget
        mock_qt_widgets.QGraphicsView = _DummyWidget

        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 61.26991118430775), (0.0, 61.26991118430775)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 13.756637061435917), (200.0, 13.756637061435917)), p_dn90)
        g_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 61.26991118430775, 0.0), (0.0, 61.26991118430775, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 13.756637061435917, 0.0), (200.0, 13.756637061435917, 0.0)), z_b02),
            ),
        )
        verts_z = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 12.5), (0.0, 12.5)),
            "P02": ((0.0, 15.013274122871834), (200.0, 15.013274122871834), (200.0, 60.013274122871834), (0.0, 60.013274122871834)),
            "P03": ((0.0, 62.52654824574367), (200.0, 62.52654824574367), (200.0, 75.02654824574367), (0.0, 75.02654824574367)),
        }
        sess_z = LoadedPartSession("Z-(ok)", None, None, g_z, verts_z, "P02", FoldState(g_z, "P02"), 0)

        with patch.object(pb2d, "_qt", return_value=(mock_qt_core, mock_qt_gui, mock_qt_widgets)):
            panel = pb2d._PreviewPanel()
            panel.draw = MagicMock()
            panel.fit = MagicMock()
            panel.session = sess_z
            panel._configure_for_active_bend()

            # 1) On initial load, all bends are 0 deg
            self.assertEqual(panel.session.fold_state.angles, {"B01": 0.0, "B02": 0.0})
            self.assertEqual(panel.angle, 0.0)

            # 2) Bend B01 from 0 -> +90 deg; B02 stays 0 deg
            panel.set_angle(45.0)
            self.assertEqual(panel.session.fold_state.angles, {"B01": 45.0, "B02": 0.0})
            panel.to_target_angle()
            self.assertEqual(panel.session.fold_state.angles, {"B01": 90.0, "B02": 0.0})

            # 3) Select operation 2 (B02): B01 keeps +90 deg, B02 starts at 0 deg (no auto-folding!)
            item_b02 = object()
            panel.operations.indexOfTopLevelItem.return_value = 1
            panel._on_operation_selected(item_b02, None)
            self.assertEqual(panel.session.active_bend_index, 1)
            self.assertEqual(panel.session.active_bend_id, "B02")
            self.assertEqual(panel.angle, 0.0)
            self.assertEqual(panel.session.fold_state.angles, {"B01": 90.0, "B02": 0.0})

            # 4) Bend B02 from 0 -> -90 deg; both bends now at target
            panel.set_angle(-45.0)
            self.assertEqual(panel.session.fold_state.angles, {"B01": 90.0, "B02": -45.0})
            panel.to_target_angle()
            self.assertEqual(panel.session.fold_state.angles, {"B01": 90.0, "B02": -90.0})

            # 5) Reset returns ALL bends to 0 deg
            panel.reset()
            self.assertEqual(panel.session.fold_state.angles, {"B01": 0.0, "B02": 0.0})
            self.assertEqual(panel.angle, 0.0)

    def test_phase4a_sequence_controller_l_z_135_pause_resume_manual_and_gui(self):
        # --- Test A: L +90 deg ---
        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        half_ba = p_up90.bend_allowance_mm / 2.0
        y_axis = 47.5 + half_ba
        z_l = make_bend_zone("B01", ((0.0, y_axis), (200.0, y_axis)), p_up90)
        g_l = PanelGraph(
            ("P01", "P02"),
            (GraphEdge("B01", "P02", "P01", 90.0, ((0.0, y_axis, 0.0), (200.0, y_axis, 0.0)), z_l),),
        )
        ctrl_l = BendSequenceController(FoldState(g_l, "P02"), speed_deg_per_sec=90.0)
        self.assertEqual(ctrl_l.status, PlaybackStatus.IDLE)
        self.assertEqual(ctrl_l.current_angle_deg, 0.0)

        ctrl_l.play_all()
        self.assertEqual(ctrl_l.status, PlaybackStatus.PLAYING)
        # Advance 5 ticks of 0.1 s (= 45 deg)
        for _ in range(5):
            ctrl_l.advance_time(0.1)
        self.assertAlmostEqual(ctrl_l.current_angle_deg, 45.0, places=9)
        self.assertEqual(ctrl_l.status, PlaybackStatus.PLAYING)
        # Advance remaining 5 ticks of 0.1 s (= 90 deg -> COMPLETED)
        for _ in range(5):
            ctrl_l.advance_time(0.1)
        self.assertAlmostEqual(ctrl_l.current_angle_deg, 90.0, places=9)
        self.assertEqual(ctrl_l.status, PlaybackStatus.COMPLETED)
        # Reset -> 0 deg, IDLE
        ctrl_l.reset()
        self.assertEqual(ctrl_l.status, PlaybackStatus.IDLE)
        self.assertEqual(ctrl_l.current_angle_deg, 0.0)

        # --- Test B: Z +90 / -90 deg ---
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 61.26991118430775), (0.0, 61.26991118430775)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 13.756637061435917), (200.0, 13.756637061435917)), p_dn90)
        g_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 61.26991118430775, 0.0), (0.0, 61.26991118430775, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 13.756637061435917, 0.0), (200.0, 13.756637061435917, 0.0)), z_b02),
            ),
        )
        ctrl_z = BendSequenceController(FoldState(g_z, "P02"), speed_deg_per_sec=90.0)
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})
        self.assertEqual(ctrl_z.status, PlaybackStatus.IDLE)

        ctrl_z.play_all()
        # 10 ticks of 0.1 s completes B01 (+90 deg) and automatically transitions to B02 (0 deg)
        for _ in range(10):
            ctrl_z.advance_time(0.1)
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 90.0, "B02": 0.0})
        self.assertEqual(ctrl_z.active_bend_id, "B02")
        self.assertEqual(ctrl_z.status, PlaybackStatus.PLAYING)

        # Next 5 ticks of 0.1 s -> B02 at -45 deg while B01 stays at +90 deg
        for _ in range(5):
            ctrl_z.advance_time(0.1)
        self.assertAlmostEqual(ctrl_z.fold_state.angles["B01"], 90.0, places=9)
        self.assertAlmostEqual(ctrl_z.fold_state.angles["B02"], -45.0, places=9)
        self.assertEqual(ctrl_z.status_line(), "Step 2/2 | B02 | -45.0° / -90.0° | PLAYING")

        # Next 5 ticks of 0.1 s -> B02 completes at -90 deg, status COMPLETED
        for _ in range(5):
            ctrl_z.advance_time(0.1)
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})
        self.assertEqual(ctrl_z.status, PlaybackStatus.COMPLETED)

        # Previous Step from COMPLETED returns (+90, 0)
        ctrl_z.previous_step()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 90.0, "B02": 0.0})
        self.assertEqual(ctrl_z.active_bend_id, "B02")
        self.assertEqual(ctrl_z.status, PlaybackStatus.PAUSED)

        # Next Step returns (+90, -90) COMPLETED
        ctrl_z.next_step()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})
        self.assertEqual(ctrl_z.status, PlaybackStatus.COMPLETED)

        # Two Previous Steps return to flat (0, 0) IDLE
        ctrl_z.previous_step()
        ctrl_z.previous_step()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})
        self.assertEqual(ctrl_z.status, PlaybackStatus.IDLE)

        # Reset returns (0, 0) IDLE
        ctrl_z.next_step()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 90.0, "B02": 0.0})
        ctrl_z.reset()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})
        self.assertEqual(ctrl_z.status, PlaybackStatus.IDLE)

        # --- Test C: 135 (+45 / -45 deg) ---
        p_up45 = bend_parameters(135.0, 1.0, 1.5, 0.4)
        p_dn45 = bend_parameters(-135.0, 2.5, 1.5, 0.4)
        z_135_1 = make_bend_zone("B01", ((200.0, 60.849421686221135), (0.0, 60.849421686221135)), p_up45)
        z_135_2 = make_bend_zone("B02", ((0.0, 14.59278462478522), (200.0, 14.59278462478522)), p_dn45)
        g_135 = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 45.0, ((200.0, 60.849421686221135, 0.0), (0.0, 60.849421686221135, 0.0)), z_135_1),
                GraphEdge("B02", "P02", "P01", -45.0, ((0.0, 14.59278462478522, 0.0), (200.0, 14.59278462478522, 0.0)), z_135_2),
            ),
        )
        ctrl_135 = BendSequenceController(FoldState(g_135, "P02"), speed_deg_per_sec=90.0)
        ctrl_135.play_all()
        for _ in range(5):
            ctrl_135.advance_time(0.1)
        self.assertEqual(ctrl_135.fold_state.angles, {"B01": 45.0, "B02": 0.0})
        self.assertEqual(ctrl_135.active_bend_id, "B02")
        for _ in range(3):
            ctrl_135.advance_time(0.1)
        self.assertAlmostEqual(ctrl_135.fold_state.angles["B01"], 45.0, places=9)
        self.assertAlmostEqual(ctrl_135.fold_state.angles["B02"], -27.0, places=9)
        for _ in range(3):
            ctrl_135.advance_time(0.1)
        self.assertEqual(ctrl_135.fold_state.angles, {"B01": 45.0, "B02": -45.0})
        self.assertEqual(ctrl_135.status, PlaybackStatus.COMPLETED)

        # --- Test D: Pause / Resume ---
        ctrl_z.reset()
        ctrl_z.play_all()
        for _ in range(4):
            ctrl_z.advance_time(0.1)
        self.assertAlmostEqual(ctrl_z.fold_state.angles["B01"], 36.0, places=9)
        ctrl_z.pause()
        self.assertEqual(ctrl_z.status, PlaybackStatus.PAUSED)
        # Several time steps while PAUSED must NOT change angles
        for _ in range(10):
            changed = ctrl_z.advance_time(0.1)
            self.assertFalse(changed)
        self.assertAlmostEqual(ctrl_z.fold_state.angles["B01"], 36.0, places=9)
        # Resume continues from 36.0 deg
        ctrl_z.resume()
        self.assertEqual(ctrl_z.status, PlaybackStatus.PLAYING)
        ctrl_z.advance_time(0.1)
        self.assertAlmostEqual(ctrl_z.fold_state.angles["B01"], 45.0, places=9)

        # --- Test E: Manual control & out-of-order protection ---
        ctrl_z.reset()
        ctrl_z.select_operation(0)
        ctrl_z.set_manual_angle(30.0)
        ctrl_z.select_operation(1)
        ctrl_z.set_manual_angle(-20.0)
        # Switch back to B01 without losing B02's angle
        ctrl_z.select_operation(0)
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 30.0, "B02": -20.0})
        # Attempting Play All on a non-prefix manual state (B01 incomplete at 30 deg while B02=-20 deg)
        # must fail deterministically with SequenceError asking for Reset
        with self.assertRaises(SequenceError):
            ctrl_z.play_all()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 30.0, "B02": -20.0})
        ctrl_z.reset()
        self.assertEqual(ctrl_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # --- Test F: GUI integration checks (single QTimer, Play All -> COMPLETED -> Play All restarts) ---
        from unittest.mock import MagicMock, patch
        import freecad_smbs.commands.pressbrake_2d as pb2d

        mock_qt_core = MagicMock()
        mock_qt_gui = MagicMock()
        mock_qt_widgets = MagicMock()

        class _DummyWidget:
            ScrollHandDrag = 1
            def __init__(self, *args, **kwargs):
                pass
            def setMinimumWidth(self, *args):
                pass
            def setMinimumHeight(self, *args):
                pass
            def setRenderHint(self, *args):
                pass
            def setDragMode(self, *args):
                pass
            def setHorizontalScrollBarPolicy(self, *args):
                pass
            def setVerticalScrollBarPolicy(self, *args):
                pass
            def setScene(self, *args):
                pass

        mock_qt_widgets.QWidget = _DummyWidget
        mock_qt_widgets.QGraphicsView = _DummyWidget

        with patch.object(pb2d, "_qt", return_value=(mock_qt_core, mock_qt_gui, mock_qt_widgets)):
            panel = pb2d._PreviewPanel()
            # Exactly one QTimer created on Panel initialization
            self.assertEqual(mock_qt_core.QTimer.call_count, 1)
            panel.draw = MagicMock()
            panel.fit = MagicMock()
            panel.session = LoadedPartSession("Z-(ok)", None, None, g_z, {}, "P02", FoldState(g_z, "P02"), 0)
            panel._configure_for_active_bend()

            # Start Play All
            panel.timer.isActive.return_value = False
            panel.play_all()
            self.assertTrue(panel.playing)
            self.assertEqual(panel.session.playback_status, PlaybackStatus.PLAYING)

            # Manual slider intervention during PLAYING stops automatic playback first
            panel.timer.isActive.return_value = True
            panel.set_angle(60.0)
            self.assertFalse(panel.playing)
            self.assertEqual(panel.session.playback_status, PlaybackStatus.PAUSED)
            self.assertEqual(panel.session.fold_state.angles, {"B01": 60.0, "B02": 0.0})
            panel.timer.stop.assert_called()

    def test_phase4b_manual_bend_order_editor_reverse_z_135_and_validation(self):
        # Reference Z-(ok) and 135-(ok) graphs & vertices
        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 61.26991118430775), (0.0, 61.26991118430775)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 13.756637061435917), (200.0, 13.756637061435917)), p_dn90)
        g_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 61.26991118430775, 0.0), (0.0, 61.26991118430775, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 13.756637061435917, 0.0), (200.0, 13.756637061435917, 0.0)), z_b02),
            ),
        )
        verts_z = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 12.5), (0.0, 12.5)),
            "P02": ((0.0, 15.013274122871834), (200.0, 15.013274122871834), (200.0, 60.013274122871834), (0.0, 60.013274122871834)),
            "P03": ((0.0, 62.52654824574367), (200.0, 62.52654824574367), (200.0, 75.02654824574367), (0.0, 75.02654824574367)),
        }

        # --- Test A & Test B: Z standard (B01 -> B02) vs reverse (B02 -> B01) ---
        sess_z = LoadedPartSession("Z-(ok)", None, None, g_z, verts_z, "P02", FoldState(g_z, "P02"), 0)
        self.assertEqual(sess_z.bend_order, ("B01", "B02"))
        self.assertEqual(sess_z.canonical_order, ("B01", "B02"))

        # Select B02 (index 1) and Move Up -> new order ("B02", "B01"), selected index becomes 0 (B02)
        sess_z.select_bend_index(1)
        self.assertEqual(sess_z.active_bend_id, "B02")
        sess_z.move_up()
        self.assertEqual(sess_z.bend_order, ("B02", "B01"))
        self.assertEqual(sess_z.active_bend_index, 0)
        self.assertEqual(sess_z.active_bend_id, "B02")
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # Play All in reverse order: B02 (-90) first, then B01 (+90)
        sess_z.play_all()
        self.assertEqual(sess_z.playback_status, PlaybackStatus.PLAYING)
        for _ in range(5):
            sess_z.advance_time(0.1)
        # B02 at -45 deg, B01 still at 0 deg
        self.assertAlmostEqual(sess_z.fold_state.angles["B01"], 0.0, places=9)
        self.assertAlmostEqual(sess_z.fold_state.angles["B02"], -45.0, places=9)
        self.assertEqual(sess_z.active_bend_id, "B02")

        # Pause & Resume during reverse order
        sess_z.pause()
        self.assertEqual(sess_z.playback_status, PlaybackStatus.PAUSED)
        sess_z.advance_time(0.1)
        self.assertAlmostEqual(sess_z.fold_state.angles["B02"], -45.0, places=9)
        sess_z.resume()
        for _ in range(5):
            sess_z.advance_time(0.1)
        # B02 finished (-90 deg), active bend automatically switched to B01 (index 1, 0 deg)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": -90.0})
        self.assertEqual(sess_z.active_bend_index, 1)
        self.assertEqual(sess_z.active_bend_id, "B01")

        # Advance 5 ticks -> B01 at +45 deg while B02 remains -90 deg
        for _ in range(5):
            sess_z.advance_time(0.1)
        self.assertAlmostEqual(sess_z.fold_state.angles["B01"], 45.0, places=9)
        self.assertAlmostEqual(sess_z.fold_state.angles["B02"], -90.0, places=9)

        # Finish B01 (+90 deg) -> COMPLETED
        for _ in range(5):
            sess_z.advance_time(0.1)
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})
        self.assertEqual(sess_z.playback_status, PlaybackStatus.COMPLETED)

        # Verify final geometry in reverse order matches 3D slice of Z-(ok).FCStd (< 1e-9 mm)
        sess_z.select_bend_index(1)  # B01 frame
        sec_z_rev = sess_z.cross_section(segments=32)
        oy_z = 61.26991118430775
        all_z_yz = (
            [(oy_z + p[0], p[1]) for p in sec_z_rev["fixed_polygon"]]
            + [(oy_z + p[0], p[1]) for p in sec_z_rev["moving_polygon"]]
            + [(oy_z + p[0], p[1]) for p in sec_z_rev["other_panel_polygons"][0][1]]
        )
        for ref_y, ref_z in (
            (12.513274122871834, -15.0),
            (14.013274122871834, -2.5),
            (15.013274122871834, 0.0),
            (60.013274122871834, -1.5),
            (61.013274122871834, 13.5),
            (62.513274122871834, 1.0),
        ):
            min_dist = min(math.hypot(y - ref_y, z - ref_z) for y, z in all_z_yz)
            self.assertLess(min_dist, 1e-9)

        # Previous Step / Next Step in reverse order ("B02", "B01"):
        # From COMPLETED (+90, -90), Previous Step unfolds the 2nd step (B01 -> 0) while keeping 1st step (B02 = -90)
        sess_z.previous_step()
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": -90.0})
        self.assertEqual(sess_z.active_bend_id, "B01")
        sess_z.next_step()
        self.assertEqual(sess_z.fold_state.angles, {"B01": 90.0, "B02": -90.0})
        self.assertEqual(sess_z.playback_status, PlaybackStatus.COMPLETED)

        # Play All from COMPLETED resets angles and replays the SAME custom order ("B02", "B01")
        sess_z.play_all()
        self.assertEqual(sess_z.bend_order, ("B02", "B01"))
        self.assertEqual(sess_z.active_bend_id, "B02")
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # Reset keeps custom order ("B02", "B01") and flattens angles
        sess_z.reset_all_bends()
        self.assertEqual(sess_z.bend_order, ("B02", "B01"))
        self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # --- Test D: Move Down & Restore Order ---
        sess_z.select_bend_index(0)  # B02 at index 0
        sess_z.move_down()
        self.assertEqual(sess_z.bend_order, ("B01", "B02"))
        self.assertEqual(sess_z.active_bend_index, 1)
        self.assertEqual(sess_z.active_bend_id, "B02")
        sess_z.move_up()
        self.assertEqual(sess_z.bend_order, ("B02", "B01"))
        sess_z.restore_canonical_order()
        self.assertEqual(sess_z.bend_order, ("B01", "B02"))

        # --- Test C: 135-(ok) reverse order (B02 -45 -> B01 +45) ---
        p_up45 = bend_parameters(135.0, 1.0, 1.5, 0.4)
        p_dn45 = bend_parameters(-135.0, 2.5, 1.5, 0.4)
        z_135_1 = make_bend_zone("B01", ((200.0, 60.849421686221135), (0.0, 60.849421686221135)), p_up45)
        z_135_2 = make_bend_zone("B02", ((0.0, 14.59278462478522), (200.0, 14.59278462478522)), p_dn45)
        g_135 = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 45.0, ((200.0, 60.849421686221135, 0.0), (0.0, 60.849421686221135, 0.0)), z_135_1),
                GraphEdge("B02", "P02", "P01", -45.0, ((0.0, 14.59278462478522, 0.0), (200.0, 14.59278462478522, 0.0)), z_135_2),
            ),
        )
        verts_135 = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 13.964466094067262), (0.0, 13.964466094067262)),
            "P02": ((0.0, 15.22110315550318), (200.0, 15.22110315550318), (200.0, 60.22110315550318), (0.0, 60.22110315550318)),
            "P03": ((0.0, 61.477740216939095), (200.0, 61.477740216939095), (200.0, 75.44220631100636), (0.0, 75.44220631100636)),
        }
        sess_135 = LoadedPartSession("135-(ok)", None, None, g_135, verts_135, "P02", FoldState(g_135, "P02"), 0)
        sess_135.set_bend_order(("B02", "B01"))
        sess_135.play_all()
        for _ in range(5):
            sess_135.advance_time(0.1)
        self.assertEqual(sess_135.fold_state.angles, {"B01": 0.0, "B02": -45.0})
        self.assertEqual(sess_135.active_bend_id, "B01")
        for _ in range(5):
            sess_135.advance_time(0.1)
        self.assertEqual(sess_135.fold_state.angles, {"B01": 45.0, "B02": -45.0})
        self.assertEqual(sess_135.playback_status, PlaybackStatus.COMPLETED)
        sec_135 = sess_135.cross_section(segments=32)
        all_135_pts = (
            list(sec_135["fixed_polygon"])
            + list(sec_135["moving_polygon"])
            + list(sec_135["other_panel_polygons"][0][1])
        )
        s_135 = [p[0] for p in all_135_pts]
        z_135 = [p[1] for p in all_135_pts]
        self.assertAlmostEqual(max(s_135) - min(s_135), 68.2842712474619, places=9)
        self.assertAlmostEqual(max(z_135) - min(z_135), 21.83452377915607, places=9)

        # --- Test E: Invalid order rejected atomically ---
        sess_z.reset_all_bends()
        sess_z.set_bend_order(("B02", "B01"))
        for bad_order in (
            ("B01",),                   # incomplete / missing ID
            ("B01", "B01"),             # duplicate ID
            ("B01", "B02", "B03"),      # extra ID
            ("B01", "B99"),             # unknown ID
            "B01",                      # string instead of sequence
        ):
            with self.assertRaises(SequenceError):
                sess_z.set_bend_order(bad_order)
            self.assertEqual(sess_z.bend_order, ("B02", "B01"))
            self.assertEqual(sess_z.fold_state.angles, {"B01": 0.0, "B02": 0.0})

        # --- Test F: Reordering rejected during PLAYING, PAUSED with non-zero angle, and COMPLETED ---
        sess_z.play_all()
        with self.assertRaises(SequenceError):
            sess_z.move_down()
        with self.assertRaises(SequenceError):
            sess_z.set_bend_order(("B01", "B02"))
        sess_z.advance_time(0.1)
        sess_z.pause()
        self.assertEqual(sess_z.playback_status, PlaybackStatus.PAUSED)
        with self.assertRaises(SequenceError):
            sess_z.move_down()
        with self.assertRaises(SequenceError):
            sess_z.restore_canonical_order()
        sess_z.fold_all_to_target()
        self.assertEqual(sess_z.playback_status, PlaybackStatus.COMPLETED)
        with self.assertRaises(SequenceError):
            sess_z.set_bend_order(("B01", "B02"))
        self.assertEqual(sess_z.bend_order, ("B02", "B01"))

    def test_phase5a_custom_punch_and_die_tooling_system(self):
        """Phase 5A Tests A–G: DXF/STEP/FreeCAD custom tooling import, datums, positioning, library persistence, and invalid geometry rejection."""
        import tempfile
        from pathlib import Path
        from smbs_core.kinematics2d.tooling_25d import (
            ArcSegment2D,
            Circle2D,
            LineSegment2D,
            NOT_SPECIFIED,
            ToolingError,
            ToolingSetup2D,
            ToolKind,
            ToolLibrary25D,
            ValidationStatus,
            compare_profiles_within_tolerance,
            import_tool_from_dxf,
            machine_to_view_point,
            view_to_machine_point,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # ------------------------------------------------------------------
            # Test A — DXF Punch with real circular tip arc and lightening hole
            # ------------------------------------------------------------------
            # Build a symmetric punch profile:
            # - Tip arc centered at (100.0, 51.0), R = 1.0 mm, from 210 deg to 330 deg
            #   -> start = (100 - cos(30), 51 - sin(30)) = (99.1339746, 50.5)
            #   -> end   = (100 + cos(30), 51 - sin(30)) = (100.8660254, 50.5)
            #   -> lowest point = (100.0, 50.0)
            # - Left flank from (90.0, 70.0) to (99.1339746, 50.5)
            # - Right flank from (100.8660254, 50.5) to (110.0, 70.0)
            # - Left vertical body from (90.0, 150.0) to (90.0, 70.0)
            # - Right vertical body from (110.0, 70.0) to (110.0, 150.0)
            # - Top mounting base from (110.0, 150.0) to (90.0, 150.0)
            # - Inner mounting hole CIRCLE at (100.0, 135.0), R = 3.0 mm
            x_l = 100.0 - math.cos(math.radians(30.0)) * 1.0
            x_r = 100.0 + math.cos(math.radians(30.0)) * 1.0
            y_arc = 51.0 - math.sin(math.radians(30.0)) * 1.0

            punch_dxf = tmp_path / "custom_punch_r1.dxf"
            punch_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "HEADER",
                    "9", "$INSUNITS", "70", "4",
                    "0", "ENDSEC",
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "ARC",
                    "10", "100.0", "20", "51.0", "40", "1.0", "50", "210.0", "51", "330.0",
                    "0", "LINE",
                    "10", f"{x_r:.8f}", "20", f"{y_arc:.8f}", "11", "110.0", "21", "70.0",
                    "0", "LINE",
                    "10", "110.0", "20", "70.0", "11", "110.0", "21", "150.0",
                    "0", "LINE",
                    "10", "110.0", "20", "150.0", "11", "90.0", "21", "150.0",
                    "0", "LINE",
                    "10", "90.0", "20", "150.0", "11", "90.0", "21", "70.0",
                    "0", "LINE",
                    "10", "90.0", "20", "70.0", "11", f"{x_l:.8f}", "21", f"{y_arc:.8f}",
                    "0", "CIRCLE",
                    "10", "100.0", "20", "135.0", "40", "3.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )

            punch = import_tool_from_dxf(punch_dxf, kind=ToolKind.PUNCH, name="P-01")
            self.assertEqual(punch.kind, ToolKind.PUNCH)
            self.assertEqual(punch.name, "P-01")
            self.assertEqual(punch.units, "mm")
            self.assertEqual(punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
            self.assertAlmostEqual(punch.tip_radius_mm, 1.0, places=5)
            self.assertAlmostEqual(punch.height_mm, 100.0, places=5)
            self.assertEqual(punch.maximum_load, NOT_SPECIFIED)
            self.assertEqual(punch.length_mm, NOT_SPECIFIED)
            self.assertEqual(punch.v_opening_mm, NOT_SPECIFIED)
            self.assertEqual(punch.profile_geometry.line_count, 5)
            self.assertEqual(punch.profile_geometry.arc_count, 2)  # 1 tip ARC + 1 hole CIRCLE
            self.assertEqual(len(punch.profile_geometry.holes), 1)
            self.assertIsNotNone(punch.punch_datums)
            self.assertAlmostEqual(punch.punch_datums.working_tip[0], 100.0, places=5)
            self.assertAlmostEqual(punch.punch_datums.working_tip[1], 50.0, places=5)
            self.assertEqual(punch.bbox, (-10.0, 0.0, 10.0, 100.0))

            # Also verify real project DXF punches if present on disk
            real_dxf = Path("/Users/badmc/Desktop/bend_Seq/1015 88 R 0_8 IH.dxf")
            if real_dxf.is_file():
                real_p = import_tool_from_dxf(real_dxf, kind=ToolKind.PUNCH)
                self.assertEqual(real_p.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
                self.assertAlmostEqual(real_p.tip_radius_mm, 0.8, places=4)
                self.assertEqual(real_p.profile_geometry.arc_count, 4)

            # ------------------------------------------------------------------
            # Test B — DXF Die with known V-opening, shoulder radii, and depth
            # ------------------------------------------------------------------
            # Build a V12 90-deg die with R_shoulder = 1.5 mm, top y = 60.0, bottom y = 0.0:
            # Left top flat: x in [0, 24.0], y = 60.0
            # Left shoulder arc: center (24.0, 58.5), R = 1.5, from 45 deg to 90 deg
            #   -> at 90 deg: (24.0, 60.0)
            #   -> at 45 deg: (24.0 + 1.5*cos(45), 58.5 + 1.5*sin(45)) = (25.06066017, 59.56066017)
            #   Tangent line at 45 deg has slope -1 (x + y = 84.62132034 -> at y = 60, x_extrap = 24.62132034)
            # Right symmetric shoulder arc: center (36.0, 58.5), R = 1.5, from 90 deg to 135 deg
            #   -> at 135 deg: (36.0 - 1.5*cos(45), 58.5 + 1.5*sin(45)) = (34.93933983, 59.56066017)
            #   -> at 90 deg: (36.0, 60.0)
            # V-bottom apex at x = 30.0: y_apex = 59.56066017 - (30.0 - 25.06066017) = 54.62132034
            c45 = 1.5 * math.cos(math.radians(45.0))
            s45 = 1.5 * math.sin(math.radians(45.0))
            lx_tan = 24.0 + c45
            rx_tan = 36.0 - c45
            y_tan = 58.5 + s45
            y_apex = y_tan - (30.0 - lx_tan)

            die_dxf = tmp_path / "custom_die_v12.dxf"
            die_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "HEADER",
                    "9", "$INSUNITS", "70", "4",
                    "0", "ENDSEC",
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "LINE", "10", "0.0", "20", "0.0", "11", "60.0", "21", "0.0",
                    "0", "LINE", "10", "60.0", "20", "0.0", "11", "60.0", "21", "60.0",
                    "0", "LINE", "10", "60.0", "20", "60.0", "11", "36.0", "21", "60.0",
                    "0", "ARC", "10", "36.0", "20", "58.5", "40", "1.5", "50", "90.0", "51", "135.0",
                    "0", "LINE", "10", f"{rx_tan:.8f}", "20", f"{y_tan:.8f}", "11", "30.0", "21", f"{y_apex:.8f}",
                    "0", "LINE", "10", "30.0", "20", f"{y_apex:.8f}", "11", f"{lx_tan:.8f}", "21", f"{y_tan:.8f}",
                    "0", "ARC", "10", "24.0", "20", "58.5", "40", "1.5", "50", "45.0", "51", "90.0",
                    "0", "LINE", "10", "24.0", "20", "60.0", "11", "0.0", "21", "60.0",
                    "0", "LINE", "10", "0.0", "20", "60.0", "11", "0.0", "21", "0.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )

            die = import_tool_from_dxf(die_dxf, kind=ToolKind.DIE, name="V12")
            self.assertEqual(die.kind, ToolKind.DIE)
            self.assertEqual(die.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
            self.assertAlmostEqual(die.shoulder_radius_mm, 1.5, places=5)
            self.assertAlmostEqual(die.die_angle_deg, 90.0, places=4)
            self.assertAlmostEqual(die.height_mm, 60.0, places=5)
            expected_v_opening = (36.0 - c45 + (60.0 - y_tan)) - (24.0 + c45 - (60.0 - y_tan))
            self.assertAlmostEqual(die.v_opening_mm, expected_v_opening, places=5)
            self.assertAlmostEqual(die.die_datums.v_depth_mm, 60.0 - y_apex, places=5)
            self.assertAlmostEqual(die.die_datums.v_groove_center[0], 30.0, places=5)
            self.assertAlmostEqual(die.die_datums.v_groove_center[1], 60.0, places=5)

            # ------------------------------------------------------------------
            # Test E — Positioning PUNCH and DIE in shared Machine & View coords
            # ------------------------------------------------------------------
            setup = ToolingSetup2D()
            setup.select_punch(punch)
            setup.select_die(die)
            self.assertEqual(
                setup.status_line(),
                "Punch: P-01 | Die: V12 | Geometry: VERIFIED | Collision: NOT CHECKED",
            )

            # Align to a real L/Z bend cross-section at s = 25.0 mm, z in [-1.0, +1.0]
            mock_sec = {
                "neutral_curve": [(24.0, 0.0), (25.0, 0.0), (26.0, 0.0)],
                "zone_polygon": [(24.0, -1.0), (26.0, -1.0), (26.0, 1.0), (24.0, 1.0)],
            }
            setup.align_to_cross_section(mock_sec)
            self.assertTrue(setup.aligned)
            p_tip_m = setup.punch_tip_machine()
            d_vc_m = setup.die_v_center_machine()
            self.assertEqual(p_tip_m, (25.0, 6.0))
            self.assertEqual(d_vc_m, (25.0, -1.0))
            self.assertAlmostEqual(setup.punch_clearance_mm, 5.0, places=9)
            # Working axes are coaxial along s = 25.0 mm, with Punch above Die
            self.assertAlmostEqual(p_tip_m[0], d_vc_m[0], places=9)
            self.assertGreater(p_tip_m[1], d_vc_m[1])

            # Verify explicit Machine <-> View coordinate round-trip
            v_pt = machine_to_view_point(p_tip_m)
            self.assertEqual(v_pt, (25.0, -6.0))
            self.assertEqual(view_to_machine_point(v_pt), p_tip_m)

            # Reset tooling position
            setup.reset_position()
            self.assertFalse(setup.aligned)
            self.assertEqual(setup.punch_tip_machine(), (0.0, 15.0))
            self.assertEqual(setup.die_v_center_machine(), (0.0, -5.0))

            # ------------------------------------------------------------------
            # Test F — Library Persistence (save, reload, exact geometry match, delete)
            # ------------------------------------------------------------------
            lib1 = ToolLibrary25D(library_root=tmp_path / "lib")
            p_saved_path = lib1.save_tool(punch)
            d_saved_path = lib1.save_tool(die)
            self.assertTrue(p_saved_path.is_file())
            self.assertTrue(d_saved_path.is_file())

            # Re-instantiate a fresh ToolLibrary25D (simulating FreeCAD restart)
            lib2 = ToolLibrary25D(library_root=tmp_path / "lib")
            p_reloaded = lib2.load_tool(punch.tool_id, ToolKind.PUNCH, use_cache=False)
            d_reloaded = lib2.load_tool(die.tool_id, ToolKind.DIE, use_cache=False)

            ok_p, msg_p = compare_profiles_within_tolerance(punch, p_reloaded, tol_mm=1e-6)
            self.assertTrue(ok_p, msg_p)
            ok_d, msg_d = compare_profiles_within_tolerance(die, d_reloaded, tol_mm=1e-6)
            self.assertTrue(ok_d, msg_d)
            self.assertAlmostEqual(p_reloaded.tip_radius_mm, 1.0, places=6)
            self.assertAlmostEqual(d_reloaded.shoulder_radius_mm, 1.5, places=6)

            # Deleting without confirmation must fail closed
            with self.assertRaises(ToolingError):
                lib2.delete_tool(punch.tool_id, ToolKind.PUNCH, confirmed=False)
            lib2.delete_tool(punch.tool_id, ToolKind.PUNCH, confirmed=True)
            self.assertEqual(len(lib2.list_tools(ToolKind.PUNCH)), 0)
            self.assertEqual(len(lib2.list_tools(ToolKind.DIE)), 1)

            # ------------------------------------------------------------------
            # Test G — Invalid Geometry (open contour, self-intersection, missing datums,
            #          bad scale, corrupt file) without corrupting existing library
            # ------------------------------------------------------------------
            # G1. Open contour
            open_dxf = tmp_path / "open_contour.dxf"
            open_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "LINE", "10", "0.0", "20", "0.0", "11", "20.0", "21", "0.0",
                    "0", "LINE", "10", "20.0", "20", "0.0", "11", "20.0", "21", "50.0",
                    "0", "LINE", "10", "20.0", "20", "50.0", "11", "0.0", "21", "50.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ToolingError):
                import_tool_from_dxf(open_dxf, kind=ToolKind.PUNCH)

            # G2. Self-intersecting bow-tie contour
            bowtie_dxf = tmp_path / "bowtie.dxf"
            bowtie_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "LINE", "10", "0.0", "20", "0.0", "11", "20.0", "21", "40.0",
                    "0", "LINE", "10", "20.0", "20", "40.0", "11", "0.0", "21", "40.0",
                    "0", "LINE", "10", "0.0", "20", "40.0", "11", "20.0", "21", "0.0",
                    "0", "LINE", "10", "20.0", "20", "0.0", "11", "0.0", "21", "0.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ToolingError):
                import_tool_from_dxf(bowtie_dxf, kind=ToolKind.PUNCH)

            # G3. Ambiguous / missing datums (plain rectangle without punch tip or die V-groove)
            rect_dxf = tmp_path / "plain_rect.dxf"
            rect_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "LINE", "10", "0.0", "20", "0.0", "11", "20.0", "21", "0.0",
                    "0", "LINE", "10", "20.0", "20", "0.0", "11", "20.0", "21", "50.0",
                    "0", "LINE", "10", "20.0", "20", "50.0", "11", "0.0", "21", "50.0",
                    "0", "LINE", "10", "0.0", "20", "50.0", "11", "0.0", "21", "0.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )
            ambig_punch = import_tool_from_dxf(rect_dxf, kind=ToolKind.PUNCH)
            self.assertEqual(ambig_punch.validation_status, ValidationStatus.NEEDS_ALIGNMENT)
            # Aligning a tool with NEEDS_ALIGNMENT must fail closed until manual datums are assigned
            setup_ambig = ToolingSetup2D(punch=ambig_punch)
            with self.assertRaises(ToolingError):
                setup_ambig.align_to_cross_section(mock_sec)
            # Once user assigns datums explicitly, status becomes GEOMETRY_VERIFIED
            ambig_punch.assign_punch_datums(
                working_tip=(10.0, 0.0),
                mounting_base=((0.0, 50.0), (20.0, 50.0)),
            )
            self.assertEqual(ambig_punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)

            # G4. Wrong scale (10000 mm x 10000 mm profile when imported as meters)
            bad_scale_punch = import_tool_from_dxf(punch_dxf, kind=ToolKind.PUNCH, units_override="m")
            self.assertEqual(bad_scale_punch.validation_status, ValidationStatus.INVALID)
            with self.assertRaises(ToolingError):
                lib2.save_tool(bad_scale_punch)

            # G5. Corrupt file
            corrupt_dxf = tmp_path / "corrupt.dxf"
            corrupt_dxf.write_text("NOT_A_VALID_DXF_FILE\n123\n", encoding="utf-8")
            with self.assertRaises(ToolingError):
                import_tool_from_dxf(corrupt_dxf, kind=ToolKind.PUNCH)

            # Library still has the valid die intact
            self.assertEqual(len(lib2.list_tools(ToolKind.DIE)), 1)

            # ------------------------------------------------------------------
            # Test C & D — STEP 3D tool & FreeCAD Document Object import (when FreeCAD is available)
            # ------------------------------------------------------------------
            try:
                import FreeCAD as App
                import Part
                has_freecad = True
            except ImportError:
                has_freecad = False

            if has_freecad:
                from smbs_core.kinematics2d.tooling_25d import (
                    import_tool_from_freecad_object,
                    import_tool_from_step,
                )

                # Test D: Open puancon.FCStd, import from Body, verify source object & document are unmodified
                fc_path = Path("/Users/badmc/Desktop/SMBS_BendSeq/toollibrary/punch/puancon.FCStd")
                doc = App.openDocument(str(fc_path), True)
                try:
                    body = doc.getObject("Body")
                    orig_obj_names = tuple(o.Name for o in doc.Objects)
                    orig_placement = (
                        tuple(body.Placement.Base),
                        tuple(body.Placement.Rotation.Q),
                    )
                    orig_bbox = (
                        body.Shape.BoundBox.XMin,
                        body.Shape.BoundBox.YMin,
                        body.Shape.BoundBox.ZMin,
                        body.Shape.BoundBox.XMax,
                        body.Shape.BoundBox.YMax,
                        body.Shape.BoundBox.ZMax,
                    )

                    fc_punch = import_tool_from_freecad_object(
                        body, kind=ToolKind.PUNCH, name="FC_Punch", length_axis="Z"
                    )
                    self.assertEqual(fc_punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
                    self.assertAlmostEqual(fc_punch.tip_radius_mm, 0.8, places=4)
                    self.assertAlmostEqual(fc_punch.length_mm, 100.0, places=4)
                    self.assertAlmostEqual(fc_punch.height_mm, 134.0, places=4)

                    # Confirm source document and object were not modified
                    self.assertEqual(tuple(o.Name for o in doc.Objects), orig_obj_names)
                    self.assertEqual(
                        (tuple(body.Placement.Base), tuple(body.Placement.Rotation.Q)),
                        orig_placement,
                    )
                    after_bbox = (
                        body.Shape.BoundBox.XMin,
                        body.Shape.BoundBox.YMin,
                        body.Shape.BoundBox.ZMin,
                        body.Shape.BoundBox.XMax,
                        body.Shape.BoundBox.YMax,
                        body.Shape.BoundBox.ZMax,
                    )
                    self.assertEqual(after_bbox, orig_bbox)

                    # Test C: Export 3D tool Shape to STEP, import via import_tool_from_step, verify exact match
                    step_file = tmp_path / "puancon_test.step"
                    body.Shape.exportStep(str(step_file))
                    step_punch = import_tool_from_step(
                        step_file, kind=ToolKind.PUNCH, name="STEP_Punch", length_axis="Z"
                    )
                    self.assertEqual(step_punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
                    ok_step, msg_step = compare_profiles_within_tolerance(fc_punch, step_punch, tol_mm=1e-3)
                    self.assertTrue(ok_step, msg_step)

                    # Multi-solid ambiguity check: compound with 2 solids without solid_index must raise ToolingError
                    s1 = body.Shape.Solids[0].copy()
                    s2 = body.Shape.Solids[0].copy()
                    s2.translate(App.Vector(200.0, 0.0, 0.0))
                    comp = Part.makeCompound([s1, s2])
                    multi_step = tmp_path / "multi_solid.step"
                    comp.exportStep(str(multi_step))
                    with self.assertRaises(ToolingError):
                        import_tool_from_step(multi_step, kind=ToolKind.PUNCH)
                    # With explicit solid_index=0, import succeeds
                    step_sel = import_tool_from_step(
                        multi_step, kind=ToolKind.PUNCH, solid_index=0, length_axis="Z"
                    )
                    self.assertEqual(step_sel.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
                finally:
                    App.closeDocument(doc.Name)

    def test_phase5a_hotfix1_sheet_visibility_alignment_and_bending_with_tools(self) -> None:
        """Phase 5A Hotfix 1: verify all 10 required scenarios for sheet visibility,
        separated bounds, tall-tool Fit, positive clearance alignment, Z-layers,
        and full compatibility with bending playback and reordering.
        """
        import tempfile
        from pathlib import Path
        from smbs_core.kinematics2d.tooling_25d import (
            DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
            ToolingError,
            ToolingSetup2D,
            ToolKind,
            ToolLibrary25D,
            ValidationStatus,
            compute_scene_bounds_2d,
            import_tool_from_dxf,
        )
        from freecad_smbs.commands.pressbrake_2d import (
            LoadedPartSession,
            Z_LAYER_AUXILIARY,
            Z_LAYER_DIE,
            Z_LAYER_PUNCH,
            Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS,
            Z_LAYER_SHEET_PANELS,
            Z_LAYER_SHEET_SURFACES,
            Z_LAYER_SHEET_ZONE,
        )

        # Build a realistic 2-bend Z-part session (t=1.5 mm, R=1.0 mm, B01=+90 deg, B02=-90 deg)
        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 61.26991118430775), (0.0, 61.26991118430775)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 13.756637061435917), (200.0, 13.756637061435917)), p_dn90)
        graph_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 61.26991118430775, 0.0), (0.0, 61.26991118430775, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 13.756637061435917, 0.0), (200.0, 13.756637061435917, 0.0)), z_b02),
            ),
        )
        verts_z = {
            "P01": ((0.0, 0.0), (200.0, 0.0), (200.0, 12.5), (0.0, 12.5)),
            "P02": ((0.0, 15.013274122871834), (200.0, 15.013274122871834), (200.0, 60.013274122871834), (0.0, 60.013274122871834)),
            "P03": ((0.0, 62.52654824574367), (200.0, 62.52654824574367), (200.0, 75.02654824574367), (0.0, 75.02654824574367)),
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Build a tall custom Punch (height = 140.0 mm > 120.0 mm, R_tip = 1.0 mm)
            x_r = 100.0 + 1.0 * math.cos(math.radians(330.0))
            x_l = 100.0 + 1.0 * math.cos(math.radians(210.0))
            y_arc = 51.0 + 1.0 * math.sin(math.radians(330.0))
            tall_punch_dxf = tmp_path / "tall_punch_140mm.dxf"
            tall_punch_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "ARC", "10", "100.0", "20", "51.0", "40", "1.0", "50", "210.0", "51", "330.0",
                    "0", "LINE", "10", f"{x_r:.8f}", "20", f"{y_arc:.8f}", "11", "112.0", "21", "75.0",
                    "0", "LINE", "10", "112.0", "20", "75.0", "11", "112.0", "21", "190.0",
                    "0", "LINE", "10", "112.0", "20", "190.0", "11", "88.0", "21", "190.0",
                    "0", "LINE", "10", "88.0", "20", "190.0", "11", "88.0", "21", "75.0",
                    "0", "LINE", "10", "88.0", "20", "75.0", "11", f"{x_l:.8f}", "21", f"{y_arc:.8f}",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )
            punch = import_tool_from_dxf(tall_punch_dxf, kind=ToolKind.PUNCH, name="TallPunch140")
            self.assertEqual(punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
            self.assertGreater(punch.height_mm, 120.0)
            self.assertAlmostEqual(punch.height_mm, 140.0, places=4)

            # Build a custom V12 Die (height = 60.0 mm)
            c45 = 1.5 * math.cos(math.radians(45.0))
            s45 = 1.5 * math.sin(math.radians(45.0))
            lx_tan = 24.0 + c45
            rx_tan = 36.0 - c45
            y_tan = 58.5 + s45
            y_apex = y_tan - (30.0 - lx_tan)
            die_dxf = tmp_path / "die_v12_60mm.dxf"
            die_dxf.write_text(
                "\n".join([
                    "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
                    "0", "SECTION", "2", "ENTITIES",
                    "0", "LINE", "10", "0.0", "20", "0.0", "11", "60.0", "21", "0.0",
                    "0", "LINE", "10", "60.0", "20", "0.0", "11", "60.0", "21", "60.0",
                    "0", "LINE", "10", "60.0", "20", "60.0", "11", "36.0", "21", "60.0",
                    "0", "ARC", "10", "36.0", "20", "58.5", "40", "1.5", "50", "90.0", "51", "135.0",
                    "0", "LINE", "10", f"{rx_tan:.8f}", "20", f"{y_tan:.8f}", "11", "30.0", "21", f"{y_apex:.8f}",
                    "0", "LINE", "10", "30.0", "20", f"{y_apex:.8f}", "11", f"{lx_tan:.8f}", "21", f"{y_tan:.8f}",
                    "0", "ARC", "10", "24.0", "20", "58.5", "40", "1.5", "50", "45.0", "51", "90.0",
                    "0", "LINE", "10", "24.0", "20", "60.0", "11", "0.0", "21", "60.0",
                    "0", "LINE", "10", "0.0", "20", "60.0", "11", "0.0", "21", "0.0",
                    "0", "ENDSEC", "0", "EOF",
                ]) + "\n",
                encoding="utf-8",
            )
            die = import_tool_from_dxf(die_dxf, kind=ToolKind.DIE, name="DieV12")
            self.assertEqual(die.validation_status, ValidationStatus.GEOMETRY_VERIFIED)

            # Check 7 & 8 (Headless geometry level): Align Tooling positive clearance & coaxial working axes
            session = LoadedPartSession(
                part_label="Z_Part",
                unfold_data=None,
                panel_build=None,
                graph=graph_z,
                panel_vertices_xy=verts_z,
                fixed_panel="P02",
                fold_state=FoldState(graph_z, "P02"),
            )
            setup = ToolingSetup2D()
            # Align with no tools selected must raise ToolingError (never silent no-op)
            with self.assertRaises(ToolingError):
                setup.align_to_cross_section(session.cross_section(segments=16))

            setup.select_punch(punch)
            setup.select_die(die)
            sec_b01 = session.cross_section(segments=16)
            setup.align_to_cross_section(sec_b01, bend_id=session.active_bend_id)
            self.assertTrue(setup.aligned)
            self.assertEqual(setup.aligned_bend_id, "B01")
            self.assertGreater(setup.punch_clearance_mm, 0.0)
            self.assertAlmostEqual(setup.punch_clearance_mm, DEFAULT_INITIAL_PUNCH_CLEARANCE_MM, places=6)

            # Verify working axes coincide with active bend line and punch is above sheet while die is below
            anchor_s = sum(p[0] for p in sec_b01["neutral_curve"]) / len(sec_b01["neutral_curve"])
            sheet_zs = [p[1] for p in sec_b01["zone_polygon"]]
            sheet_top_z = max(sheet_zs)
            sheet_bot_z = min(sheet_zs)
            ptip = setup.punch_tip_machine()
            dvc = setup.die_v_center_machine()
            self.assertAlmostEqual(ptip[0], anchor_s, places=6)
            self.assertAlmostEqual(dvc[0], anchor_s, places=6)
            self.assertAlmostEqual(ptip[1], sheet_top_z + DEFAULT_INITIAL_PUNCH_CLEARANCE_MM, places=6)
            self.assertAlmostEqual(dvc[1], sheet_bot_z, places=6)
            self.assertGreater(ptip[1], sheet_top_z)
            self.assertLessEqual(dvc[1], sheet_bot_z)

            # Check 5 & 6 (Headless bounds level): separated sheet_bounds, punch_bounds, die_bounds, combined_bounds
            sheet_pts = list(sec_b01["inner_surface"]) + list(sec_b01["outer_surface"])
            punch_pts = list(setup.punch_polygons_machine()[0])
            die_pts = list(setup.die_polygons_machine()[0])
            bounds = compute_scene_bounds_2d(
                sheet_pts=sheet_pts,
                punch_pts=punch_pts,
                die_pts=die_pts,
                focus_sheet_when_tall_tooling=True,
            )
            self.assertIsNotNone(bounds.sheet_bounds)
            self.assertIsNotNone(bounds.punch_bounds)
            self.assertIsNotNone(bounds.die_bounds)
            self.assertIsNotNone(bounds.combined_bounds)
            # Combined vertical span in Machine Z is from die bottom (sheet_bot_z - 60.0) to punch top (sheet_top_z + 5.0 + 140.0)
            self.assertAlmostEqual(bounds.combined_bounds[1], sheet_bot_z - 60.0, places=4)
            self.assertAlmostEqual(
                bounds.combined_bounds[3],
                sheet_top_z + DEFAULT_INITIAL_PUNCH_CLEARANCE_MM + 140.0,
                places=4,
            )
            # Focused view_rect keeps vertical height much tighter than the full tool stack so the sheet is clearly visible
            _, _, vw, vh = bounds.view_rect
            self.assertLess(vh, 130.0)
            # And sheet view bounds are fully contained inside view_rect
            rx, ry, rw, rh = bounds.view_rect
            sheet_min_s, sheet_min_z, sheet_max_s, sheet_max_z = bounds.sheet_bounds
            self.assertLessEqual(rx, sheet_min_s)
            self.assertGreaterEqual(rx + rw, sheet_max_s)
            self.assertLessEqual(ry, -sheet_max_z)
            self.assertGreaterEqual(ry + rh, -sheet_min_z)

            # Now test real Qt QGraphicsScene / Panel widget if PySide is available
            try:
                from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, _qt
                QtCore, QtGui, QtWidgets = _qt()
                has_qt = True
            except Exception:
                has_qt = False

            if has_qt:
                app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
                panel = _PreviewPanel()
                panel.resize(480, 640)
                panel.show()

                def role_items(role_name: str):
                    return [it for it in panel.scene.items() if it.data(0) == role_name]

                def sheet_items():
                    return [
                        it for it in panel.scene.items()
                        if isinstance(it.data(0), str) and it.data(0).startswith("sheet_")
                    ]

                # --------------------------------------------------------------
                # Check 1 (Scenario A): Sheet loaded without tools
                # --------------------------------------------------------------
                panel.session = LoadedPartSession(
                    part_label="Z_Part",
                    unfold_data=None,
                    panel_build=None,
                    graph=graph_z,
                    panel_vertices_xy=verts_z,
                    fixed_panel="P02",
                    fold_state=FoldState(graph_z, "P02"),
                )
                panel._populate_operations()
                panel._configure_for_active_bend()
                panel._set_playback_controls_enabled(True)
                panel.draw()
                panel.fit()

                self.assertIsNotNone(panel.session)
                self.assertTrue( all(abs(v) < 1e-9 for v in panel.session.fold_state.angles.values()) )
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 0)
                self.assertEqual(len(role_items("die")), 0)
                self.assertIsNotNone(panel.last_scene_bounds)
                self.assertIsNotNone(panel.last_scene_bounds.sheet_bounds)
                self.assertIsNone(panel.last_scene_bounds.punch_bounds)
                self.assertIsNone(panel.last_scene_bounds.die_bounds)

                # --------------------------------------------------------------
                # Check 2 (Scenario B): Sheet + Punch only
                # --------------------------------------------------------------
                panel.select_punch(punch)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 1)
                self.assertEqual(len(role_items("die")), 0)
                self.assertIsNotNone(panel.last_scene_bounds.sheet_bounds)
                self.assertIsNotNone(panel.last_scene_bounds.punch_bounds)
                self.assertIsNone(panel.last_scene_bounds.die_bounds)

                # --------------------------------------------------------------
                # Check 3 (Scenario C): Sheet + Die only
                # --------------------------------------------------------------
                panel.select_punch(None)
                panel.select_die(die)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 0)
                self.assertEqual(len(role_items("die")), 1)
                self.assertIsNotNone(panel.last_scene_bounds.sheet_bounds)
                self.assertIsNone(panel.last_scene_bounds.punch_bounds)
                self.assertIsNotNone(panel.last_scene_bounds.die_bounds)

                # --------------------------------------------------------------
                # Check 4 (Scenario D): Sheet + Punch + Die + QGraphicsScene Z-Layers
                # --------------------------------------------------------------
                panel.select_punch(punch)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 1)
                self.assertEqual(len(role_items("die")), 1)
                p_item = role_items("punch")[0]
                d_item = role_items("die")[0]
                self.assertEqual(d_item.zValue(), Z_LAYER_DIE)
                self.assertEqual(p_item.zValue(), Z_LAYER_PUNCH)
                for s_item in sheet_items():
                    self.assertGreater(
                        s_item.zValue(),
                        p_item.zValue(),
                        f"Sheet item {s_item.data(0)} must have higher zValue than punch",
                    )
                    self.assertGreater(
                        s_item.zValue(),
                        d_item.zValue(),
                        f"Sheet item {s_item.data(0)} must have higher zValue than die",
                    )
                self.assertEqual(role_items("sheet_fixed")[0].zValue(), Z_LAYER_SHEET_PANELS)
                self.assertEqual(role_items("sheet_moving")[0].zValue(), Z_LAYER_SHEET_PANELS)
                self.assertEqual(role_items("sheet_zone")[0].zValue(), Z_LAYER_SHEET_ZONE)
                self.assertEqual(role_items("sheet_inner_surface")[0].zValue(), Z_LAYER_SHEET_SURFACES)
                self.assertEqual(role_items("sheet_neutral")[0].zValue(), Z_LAYER_SHEET_NEUTRAL_AND_TANGENTS)

                # --------------------------------------------------------------
                # Check 5 & 6: Visible coordinates, sceneRect, and Fit with tall punch (>120 mm)
                # --------------------------------------------------------------
                panel.fit()
                srect = panel.scene.sceneRect()
                self.assertFalse(srect.isEmpty())
                # Sheet fixed & moving polygons must lie completely inside sceneRect
                for s_item in role_items("sheet_fixed") + role_items("sheet_moving") + role_items("sheet_zone"):
                    ib = s_item.sceneBoundingRect()
                    self.assertFalse(ib.isEmpty())
                    self.assertTrue(srect.intersects(ib))
                # With 140 mm tall punch, sceneRect height stays focused (< 130 mm) so sheet is not a 1px speck
                self.assertLess(srect.height(), 130.0)

                # --------------------------------------------------------------
                # Check 7 & 8: Align Tooling positive clearance & working axis coincidence in UI
                # --------------------------------------------------------------
                panel.align_tooling()
                self.assertTrue(panel.tooling_setup.aligned)
                self.assertAlmostEqual(
                    panel.tooling_setup.punch_clearance_mm,
                    DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
                    places=6,
                )
                self.assertAlmostEqual(
                    panel.tooling_setup.punch_tip_machine()[0],
                    panel.tooling_setup.die_v_center_machine()[0],
                    places=6,
                )

                # --------------------------------------------------------------
                # Check 9: Play All with simultaneously displayed tools
                # --------------------------------------------------------------
                panel.play_all()
                self.assertTrue(panel.playing)
                for _ in range(80):
                    if not panel.playing:
                        break
                    panel.session.advance_time(0.05)
                    panel._configure_for_active_bend()
                    panel._sync_tooling_to_active_bend()
                    panel.draw()
                    # Sheet and tools must both remain in scene at every animation step
                    self.assertGreaterEqual(len(sheet_items()), 6)
                    self.assertEqual(len(role_items("punch")), 1)
                    self.assertEqual(len(role_items("die")), 1)
                    if panel.session.playback_status.value == "COMPLETED":
                        panel._stop_timer()
                        break

                self.assertAlmostEqual(panel.session.fold_state.angles["B01"], 90.0, places=5)
                self.assertAlmostEqual(panel.session.fold_state.angles["B02"], -90.0, places=5)
                # Tooling has re-aligned to B02 when active bend switched to B02
                self.assertEqual(panel.tooling_setup.aligned_bend_id, "B02")

                # --------------------------------------------------------------
                # Check 10 (Scenarios E & F + Reset + Manual Bend Order with tools):
                # --------------------------------------------------------------
                # Reset (`|<`) returns sheet to 0 deg flat while keeping tools & sheet visible
                panel.reset()
                self.assertAlmostEqual(panel.session.fold_state.angles["B01"], 0.0, places=6)
                self.assertAlmostEqual(panel.session.fold_state.angles["B02"], 0.0, places=6)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 1)
                self.assertEqual(len(role_items("die")), 1)

                # Scenario E: Hide Tooling -> tools hidden, sheet remains visible
                panel.toggle_tooling_visibility()
                self.assertFalse(panel.tooling_setup.visible)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 0)
                self.assertEqual(len(role_items("die")), 0)

                # Scenario F: Show Tooling -> tools and sheet both visible
                panel.toggle_tooling_visibility()
                self.assertTrue(panel.tooling_setup.visible)
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 1)
                self.assertEqual(len(role_items("die")), 1)

                # Manual Bend Order (`Move Down` / `Restore Order`) with tools attached
                panel.move_selected_down()
                self.assertEqual(panel.session.bend_order, ("B02", "B01"))
                self.assertGreaterEqual(len(sheet_items()), 6)
                self.assertEqual(len(role_items("punch")), 1)
                self.assertEqual(len(role_items("die")), 1)

                panel.restore_order()
                self.assertEqual(panel.session.bend_order, ("B01", "B02"))
                self.assertGreaterEqual(len(sheet_items()), 6)

                panel.close()

            # Check ActiveDocument preservation when ToolLibrary25D scans legacy .FCStd tools
            try:
                import FreeCAD as App
                has_fc_app = True
            except ImportError:
                has_fc_app = False

            if has_fc_app:
                l_path = Path("/Users/badmc/Desktop/bend_Seq/L-(ok).FCStd")
                if l_path.is_file():
                    user_doc = App.openDocument(str(l_path))
                    try:
                        App.setActiveDocument(user_doc.Name)
                        self.assertEqual(App.ActiveDocument.Name, user_doc.Name)
                        # Scanning the legacy FCStd tool library must NEVER clobber App.ActiveDocument!
                        lib_fc = ToolLibrary25D()
                        tools_fc = lib_fc.list_tools()
                        self.assertGreaterEqual(len(tools_fc), 2)
                        self.assertIsNotNone(App.ActiveDocument)
                        self.assertEqual(App.ActiveDocument.Name, user_doc.Name)
                    finally:
                        App.closeDocument(user_doc.Name)


if __name__ == "__main__":
    unittest.main()
