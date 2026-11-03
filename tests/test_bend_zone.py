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
        sess_z.fold_all_to_target()
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
        sess_135.fold_all_to_target()
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

        # 1) Empty selection with active document: warning returned, pipeline NOT called, session & angle preserved
        outcome1, sess1, msg1 = execute_load_part(existing_session, _Doc(), [], build_fn=_forbidden_build)
        self.assertEqual(outcome1, "warning")
        self.assertIs(sess1, existing_session)
        self.assertAlmostEqual(sess1.current_angle_deg, 45.0, places=9)
        self.assertEqual(len(pipeline_calls), 0)
        self.assertIn("Деталь не выбрана", msg1)

        # 2) Missing document: warning returned, pipeline NOT called, session & angle preserved
        outcome2, sess2, msg2 = execute_load_part(existing_session, None, ["dummy"], build_fn=_forbidden_build)
        self.assertEqual(outcome2, "warning")
        self.assertIs(sess2, existing_session)
        self.assertAlmostEqual(sess2.current_angle_deg, 45.0, places=9)
        self.assertEqual(len(pipeline_calls), 0)
        self.assertIn("Нет открытого документа", msg2)

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


if __name__ == "__main__":
    unittest.main()
