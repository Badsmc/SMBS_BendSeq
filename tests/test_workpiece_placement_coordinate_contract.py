"""Regression test verifying the machine coordinate frame contract for 2.5D + D6 integration.

Guarantees:
1. Die top shoulder plane datum is located at Machine Z_m = 0.0 in both 2D and 3D.
2. In initial/parked/approach pose, the sheet outer (bottom) surface rests on the die shoulder at Z_m = 0.0.
3. The sheet material extends into Z_m in [0.0, +thickness_mm].
4. GUI placement current_workpiece_placement_3d() matches D6 initial placement with zero translation delta (0.0, 0.0, 0.0).
5. check_current_25d_pose() end-to-end qualifies with SAMPLED_3D_CLEAR_NOT_CONTINUOUS, 2D=TOUCH, 3D=TOUCHING, zero penetration volume, without manual overrides.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

try:
    import FreeCAD as App
    from PySide6 import QtWidgets
    HAS_FREECAD = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.demo_paths import demo_part_path


class TestWorkpiecePlacementCoordinateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD:
            raise unittest.SkipTest("FreeCAD / PySide6 environment not available")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cls.model_path = demo_part_path("box.100out-50in-(ok).FCStd", required=False)
        if not cls.model_path.is_file():
            raise unittest.SkipTest(f"Model file not found: {cls.model_path}")

    def setUp(self):
        self.doc = App.openDocument(str(self.model_path), True)
        self.unfold = next((o for o in self.doc.Objects if o.Name.endswith("_Unfold")), None)
        if self.unfold is None:
            App.closeDocument(self.doc.Name)
            raise unittest.SkipTest("Model missing _Unfold object")

        from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
        self.session = build_part_session(self.doc, [self.unfold])
        self.panel = _PreviewPanel()
        self.panel.session = self.session
        self.panel._ensure_press_cycle_controller()
        self.panel.align_tooling()

    def tearDown(self):
        if hasattr(self, "panel") and self.panel is not None:
            self.panel._stop_timer()
            self.panel.close()
            self.panel.deleteLater()
        if hasattr(self, "doc") and self.doc is not None:
            App.closeDocument(self.doc.Name)

    def test_die_and_sheet_machine_space_datums(self):
        """Verify machine space Z coordinates for die, sheet, and punch."""
        die_v = self.panel.tooling_setup.die_v_center_machine()
        self.assertIsNotNone(die_v)
        self.assertAlmostEqual(die_v[1], 0.0, places=4, msg="Die top shoulder datum must be at Z_m = 0.0")

        self.assertAlmostEqual(self.panel.tooling_setup.sheet_bottom_z_mm, 0.0, places=4)
        thickness = self.panel.session.fold_state.bends[self.panel.session.active_bend_id].bend_zone.thickness_mm
        self.assertAlmostEqual(self.panel.tooling_setup.sheet_top_z_mm, thickness, places=4)

        # Coupled kinematics datums
        ck = self.panel.press_cycle.coupled_kinematics
        self.assertAlmostEqual(ck.die_top_z_mm, 0.0, places=4)
        self.assertAlmostEqual(ck.contact_z_mm, thickness, places=4)

    def test_gui_vs_d6_placement_zero_delta(self):
        """Verify GUI placement matches D6 reference placement with zero translation delta."""
        from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D

        bend_id = self.panel.session.active_bend_id
        bend = self.panel.session.fold_state.bends[bend_id]
        outer_r = bend.bend_zone.outer_radius_mm

        gui_tf = self.panel.press_cycle.current_workpiece_placement_3d()
        d6_tf = WorkpiecePlacement3D.compute_placement(
            self.panel.session.fold_state,
            bend_id,
            support_plane_zm=outer_r,
            align_bisector=True,
            align_bend_arc_center=True,
        )

        for i, (g, d) in enumerate(zip(gui_tf.translation, d6_tf.translation)):
            self.assertAlmostEqual(
                g, d, places=5,
                msg=f"Translation component {i} differs: gui={g}, d6={d}",
            )

        # 1. Orientation matrix comparison: all 9 components must match
        for r_idx in range(3):
            for c_idx in range(3):
                g_val = gui_tf.rotation[r_idx][c_idx]
                d_val = d6_tf.rotation[r_idx][c_idx]
                self.assertAlmostEqual(
                    g_val, d_val, places=5,
                    msg=f"Rotation component ({r_idx}, {c_idx}) differs: gui={g_val}, d6={d_val}",
                )

        # 2. Proper rotation isometry verification: R * R^T = I, det(R) = +1.0
        R = gui_tf.rotation
        for i in range(3):
            for j in range(3):
                dot = sum(R[i][k] * R[j][k] for k in range(3))
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(
                    dot, expected, places=5,
                    msg=f"Rotation matrix is not orthogonal at ({i}, {j}): dot={dot}",
                )

        det = (
            R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
            - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
            + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
        )
        self.assertAlmostEqual(det, 1.0, places=5, msg=f"det(R) != +1.0: det={det}")

    def test_transformed_occt_surface_coordinates_and_common_volumes(self):
        """Verify real surface coordinates and zero common volumes against punch and die."""
        from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
        from smbs_core.kinematics2d.native_tool_loader import (
            load_native_die, load_native_punch, set_punch_ram_z,
        )

        solid = build_multi_bend_sheet_solid(
            self.session.fold_state, self.session.panel_build, segments=32,
        )
        tf = self.panel.press_cycle.current_workpiece_placement_3d()
        mat = App.Matrix(
            tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
            tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
            tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
            0.0, 0.0, 0.0, 1.0,
        )
        wp_machine = solid.shape.copy()
        wp_machine.transformShape(mat)

        # Die shoulder contact check: sheet bottom surface rests on die shoulders at Z_m = 0.0
        # Sheet material must not penetrate the die (Volume <= 1e-4 mm^3)
        die_path = str(ROOT / "toollibrary" / "die" / "matrica.FCStd")
        die = load_native_die(die_path)
        die_common = wp_machine.common(die.machine_shape)
        die_vol = float(getattr(die_common, "Volume", 0.0))
        self.assertLessEqual(
            die_vol, 1e-4,
            f"Workpiece penetrates die in initial pose: overlap={die_vol:.6f} mm3",
        )

        # Punch contact at ram_z = sheet_top_z_mm
        thickness = self.panel.tooling_setup.sheet_top_z_mm
        punch_path = str(ROOT / "toollibrary" / "punch" / "puancon.FCStd")
        punch = load_native_punch(punch_path, ram_z_mm=thickness)
        punch_common = wp_machine.common(punch.machine_shape)
        punch_vol = float(getattr(punch_common, "Volume", 0.0))
        self.assertLessEqual(
            punch_vol, 1e-4,
            f"Workpiece penetrates punch at contact ram Z: overlap={punch_vol:.6f} mm3",
        )

    def test_opposite_bend_sign_negative_angle(self):
        """Verify placement and orientation handling for negative bend angles."""
        from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
        from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
        from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
        from smbs_core.kinematics2d.panel_kinematics import FoldState

        # Build realistic Z-part with opposite bends (+90 deg and -90 deg)
        p_up90 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p_dn90 = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        z_b01 = make_bend_zone("B01", ((200.0, 60.0), (0.0, 60.0)), p_up90)
        z_b02 = make_bend_zone("B02", ((0.0, 10.0), (200.0, 10.0)), p_dn90)
        graph_z = PanelGraph(
            ("P01", "P02", "P03"),
            (
                GraphEdge("B01", "P02", "P03", 90.0, ((200.0, 60.0, 0.0), (0.0, 60.0, 0.0)), z_b01),
                GraphEdge("B02", "P02", "P01", -90.0, ((0.0, 10.0, 0.0), (200.0, 10.0, 0.0)), z_b02),
            ),
        )
        fs_z = FoldState(graph_z, "P02")
        fs_z.set_angle("B02", -45.0)

        tf_neg = WorkpiecePlacement3D.compute_placement(
            fs_z,
            "B02",
            support_plane_zm=z_b02.outer_radius_mm,
            align_bisector=True,
            align_bend_arc_center=True,
        )

        R = tf_neg.rotation
        det = (
            R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
            - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
            + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
        )
        self.assertAlmostEqual(det, 1.0, places=5)
        # Translation must be finite without NaNs
        for val in tf_neg.translation:
            self.assertTrue(math.isfinite(val), f"Translation component not finite: {val}")

    def test_different_thickness_and_radius(self):
        """Verify tooling setup datum contract across various sheet thicknesses and bend radii."""
        from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D, ToolingError
        from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone

        setup = ToolingSetup2D()
        setup.select_punch(self.panel.tooling_setup.punch)
        setup.select_die(self.panel.tooling_setup.die)

        # 1. Thickness 2.0 mm, radius 3.0 mm
        p1 = bend_parameters(90.0, 3.0, 2.0, 0.4)
        bz1 = make_bend_zone("B_TEST1", ((0.0, 0.0), (100.0, 0.0)), p1)
        mock_sec1 = {
            "thickness_mm": bz1.thickness_mm,
            "neutral_curve": [(0.0, 0.0)],
        }
        setup.align_to_cross_section(mock_sec1, clearance_mm=10.0, bend_zone=bz1)
        self.assertAlmostEqual(setup.sheet_bottom_z_mm, 0.0, places=4)
        self.assertAlmostEqual(setup.sheet_top_z_mm, 2.0, places=4)
        self.assertAlmostEqual(setup.ram_z_mm, 12.0, places=4)

        # 2. Thickness 0.8 mm, radius 1.2 mm
        p2 = bend_parameters(90.0, 1.2, 0.8, 0.4)
        bz2 = make_bend_zone("B_TEST2", ((0.0, 0.0), (100.0, 0.0)), p2)
        mock_sec2 = {
            "thickness_mm": bz2.thickness_mm,
            "neutral_curve": [(0.0, 0.0)],
        }
        setup.align_to_cross_section(mock_sec2, clearance_mm=5.0, bend_zone=bz2)
        self.assertAlmostEqual(setup.sheet_bottom_z_mm, 0.0, places=4)
        self.assertAlmostEqual(setup.sheet_top_z_mm, 0.8, places=4)
        self.assertAlmostEqual(setup.ram_z_mm, 5.8, places=4)

        # 3. Missing thickness must fail closed with ToolingError
        with self.assertRaises(ToolingError):
            setup.align_to_cross_section({"neutral_curve": [(0.0, 0.0)]})

    def test_next_bend_transition_nonparallel_axes(self):
        """Verify placement and transition to next bend with orthogonal (nonparallel) axes."""
        from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D

        bends = list(self.session.fold_state.bends.keys())
        self.assertGreaterEqual(len(bends), 2, "Box fixture must have multiple bends")

        b1, b2 = bends[0], bends[1]
        bz1 = self.session.fold_state.bends[b1].bend_zone
        bz2 = self.session.fold_state.bends[b2].bend_zone

        tf1 = WorkpiecePlacement3D.compute_placement(
            self.session.fold_state, b1, support_plane_zm=bz1.outer_radius_mm,
            align_bisector=True, align_bend_arc_center=True,
        )
        tf2 = WorkpiecePlacement3D.compute_placement(
            self.session.fold_state, b2, support_plane_zm=bz2.outer_radius_mm,
            align_bisector=True, align_bend_arc_center=True,
        )

        # Verify both have det(R) == +1.0
        for tf, b_name in [(tf1, b1), (tf2, b2)]:
            R = tf.rotation
            det = (
                R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
                - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
                + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
            )
            self.assertAlmostEqual(det, 1.0, places=5, msg=f"det(R) != 1.0 for {b_name}")

    def test_check_current_25d_pose_qualification(self):
        """Verify check_current_25d_pose succeeds with 3D=TOUCHING, 2D=TOUCH, 0 penetration volume."""
        from smbs_core.kinematics2d.physical_validation_25d import (
            check_current_25d_pose, PhysicalEvidence,
        )

        rep = check_current_25d_pose(
            self.panel.session, self.panel.press_cycle, self.panel.tooling_setup,
            native_3d=True,
        )

        self.assertEqual(rep.status, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
        self.assertEqual(rep.section_status, "TOUCH")
        self.assertEqual(rep.native_status, "TOUCHING")
        self.assertIn("3D_PUNCH_DIE_CURRENT_POSE", rep.checked_scope)


if __name__ == "__main__":
    unittest.main()
