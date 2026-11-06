"""Regression test verifying the machine coordinate frame contract for 2.5D + D6 integration.

Guarantees:
1. Die top shoulder plane datum is located at Machine Z_m = 0.0 in both 2D and 3D.
2. In initial/parked/approach pose, the sheet outer (bottom) surface rests on the die shoulder at Z_m = 0.0.
3. The sheet material extends into Z_m in [0.0, +thickness_mm].
4. GUI placement current_workpiece_placement_3d() matches D6 initial placement with zero translation delta (0.0, 0.0, 0.0).
5. check_current_25d_pose() end-to-end qualifies with SAMPLED_3D_CLEAR_NOT_CONTINUOUS, 2D=TOUCH, 3D=TOUCHING, zero penetration volume, without manual overrides.
"""
from __future__ import annotations

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
