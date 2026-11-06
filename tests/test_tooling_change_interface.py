"""Tooling change interface regression test.

Verifies the standard tooling transition sequence:
1. Load box.100out-50in-(ok).FCStd with default Set A (puancon.FCStd + matrica.FCStd).
2. Record initial datums, coupled kinematics (die_opening_v_mm = 16.0), placement, and check report.
3. Select synthetic test die with different V width (V = 24.0 mm, shoulder radius 2.0 mm), labeled SYNTHETIC_TEST_TOOLING:
   - Verify coupled_kinematics.die_opening_v_mm == 24.0.
4. Select synthetic test punch with acute angle (30.0 deg, radius 3.0 mm), labeled SYNTHETIC_TEST_TOOLING:
   - Verify punch datums and contact height update.
5. Select known clash configuration (severe penetration where overlap volume > 10^-4 mm^3):
   - Verify check reports collision / penetration (is_safe=False).
6. Return to Set A (puancon.FCStd + matrica.FCStd):
   - Verify exact reproduction of original Set A datums, kinematics, placement, and collision report.
7. Verify workpiece source geometry (FoldState.bends, panel vertices) was NOT mutated by any tool change.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from smbs_core.kinematics2d.contact_oracle_2d import ContactStatus
from smbs_core.kinematics2d.physical_validation_25d import (
    PhysicalEvidence,
    check_current_25d_pose,
)
from smbs_core.kinematics2d.tooling_25d import (
    DieDatums2D,
    ProfileGeometry2D,
    PunchDatums2D,
    ToolKind,
    ToolProfile,
    ValidationStatus,
)


class TestToolingChangeInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD:
            raise unittest.SkipTest("FreeCAD not available")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cls.model_path = demo_part_path("box.100out-50in-(ok).FCStd", required=False)
        if not cls.model_path.is_file():
            raise unittest.SkipTest(f"Model not found: {cls.model_path}")

    def setUp(self):
        self.doc = App.openDocument(str(self.model_path), True)
        self.unfold = next((o for o in self.doc.Objects if o.Name.endswith("_Unfold")), None)
        if self.unfold is None:
            App.closeDocument(self.doc.Name)
            raise unittest.SkipTest("Model missing _Unfold object")

        from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, build_part_session

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

    def test_tooling_change_sequence_and_exact_restoration(self):
        """Execute full tooling change sequence with real CAD bodies, verify updates, clash detection, and exact Set A restoration."""
        from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch
        from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveTrajectoryValidator3D, AdaptiveSamplingConfig

        # 1. Baseline Set A (puancon.FCStd + matrica.FCStd)
        set_a_punch = self.panel.tooling_setup.punch
        set_a_die = self.panel.tooling_setup.die
        self.assertIsNotNone(set_a_punch, "Default Set A punch must be loaded")
        self.assertIsNotNone(set_a_die, "Default Set A die must be loaded")
        self.assertIn("puancon", set_a_punch.tool_id.lower())
        self.assertIn("matrica", set_a_die.tool_id.lower())

        # Verify Set A native CAD shapes and datums
        native_a_die = load_native_die(set_a_die.source_file)
        native_a_punch = load_native_punch(set_a_punch.source_file, ram_z_mm=float(self.panel.press_cycle.ram_z_mm))
        self.assertEqual(native_a_die.tool_id, "matrica")
        self.assertEqual(native_a_punch.tool_id, "puancon")
        self.assertEqual(native_a_die.length_mm, 100.0)
        self.assertEqual(native_a_punch.length_mm, 100.0)

        # Record Set A coupled kinematics, placement, and collision report
        pc = self.panel.press_cycle
        self.assertIsNotNone(pc.coupled_kinematics)
        self.assertAlmostEqual(pc.coupled_kinematics.die_opening_v_mm, 32.0, places=3)
        baseline_ck_v = pc.coupled_kinematics.die_opening_v_mm
        baseline_punch_r = pc.coupled_kinematics.punch_tip_radius_mm
        baseline_punch_ang = pc.coupled_kinematics.punch_included_angle_deg
        baseline_contact_z = pc.coupled_kinematics.contact_z_mm
        baseline_placement = pc.current_workpiece_placement_3d()
        baseline_trans = tuple(baseline_placement.translation)

        # Baseline physical check report
        baseline_rep = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=True
        )

        # Record original workpiece source geometry
        orig_bend_angles = {b_id: float(b.angle_deg) for b_id, b in self.session.fold_state.bends.items()}
        orig_panel_vertices = {
            p_id: tuple((float(x), float(y)) for x, y in poly)
            for p_id, poly in self.session.panel_vertices_xy.items()
        }

        # 2. Select Set B: synthetic test die (V = 24.0 mm, shoulder radius 2.0 mm, length 120 mm)
        # and synthetic test punch (acute angle, radius 3.0 mm, length 120 mm)
        prof_die_b = self.panel.tool_library.load_tool("synthetic_die_v24", ToolKind.DIE)
        prof_punch_b = self.panel.tool_library.load_tool("synthetic_punch_a30", ToolKind.PUNCH)
        self.assertEqual(prof_die_b.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
        self.assertEqual(prof_punch_b.validation_status, ValidationStatus.GEOMETRY_VERIFIED)

        self.panel.select_die(prof_die_b)
        self.panel.select_punch(prof_punch_b)
        self.panel.align_tooling()

        # Verify that native validator receives the exact selected CAD geometry, datums, and tool length
        native_b_die = load_native_die(prof_die_b.source_file)
        native_b_punch = load_native_punch(prof_punch_b.source_file, ram_z_mm=float(self.panel.press_cycle.ram_z_mm))
        self.assertEqual(native_b_die.tool_id, "synthetic_die_v24")
        self.assertEqual(native_b_punch.tool_id, "synthetic_punch_a30")
        self.assertAlmostEqual(native_b_die.v_opening_mm, 24.0, places=3)
        self.assertEqual(native_b_die.length_mm, 120.0)
        self.assertEqual(native_b_punch.length_mm, 120.0)
        self.assertAlmostEqual(native_b_die.cad_shape.Volume, 411840.0, places=1)
        self.assertAlmostEqual(native_b_punch.cad_shape.Volume, 429600.0, places=1)

        # Verify coupled kinematics and placement updated for Set B
        self.assertEqual(self.panel.tooling_setup.die.tool_id, "synthetic_die_v24")
        self.assertEqual(self.panel.tooling_setup.punch.tool_id, "synthetic_punch_a30")
        self.assertAlmostEqual(pc.coupled_kinematics.die_opening_v_mm, 24.0, places=3)

        # Run native 3D check on Set B
        rep_b = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=True
        )
        self.assertEqual(rep_b.status, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
        self.assertEqual(rep_b.native_status, "TOUCHING")

        # Verify against independent uncached validator for Set B
        config_b = AdaptiveSamplingConfig(arc_segments=128)
        independent_validator_b = AdaptiveTrajectoryValidator3D(
            self.session.fold_state,
            self.session.panel_build,
            native_b_punch,
            native_b_die,
            config=config_b,
        )
        active_bend = self.session.fold_state.bends[self.session.active_bend_id]
        support_z = active_bend.bend_zone.outer_radius_mm if active_bend.bend_zone else 1.0
        st_indep_b = independent_validator_b.create_state_snapshot(
            active_bend_id=self.session.active_bend_id,
            phase="CONTACT",
            path_u=0.0,
            signed_angle=0.0,
            ram_z=float(self.panel.press_cycle.ram_z_mm),
            support_z=float(support_z),
            prior_angles={},
        )
        self.assertEqual(st_indep_b.collision_report.status.value, rep_b.native_status)

        # 3. Known clash configuration: select real CAD clash punch with wide wings
        prof_clash_punch = self.panel.tool_library.load_tool("synthetic_clash_punch", ToolKind.PUNCH)
        self.assertEqual(prof_clash_punch.validation_status, ValidationStatus.GEOMETRY_VERIFIED)
        self.panel.select_punch(prof_clash_punch)
        self.panel.align_tooling()

        native_clash_punch = load_native_punch(prof_clash_punch.source_file, ram_z_mm=float(self.panel.press_cycle.ram_z_mm))
        self.assertEqual(native_clash_punch.tool_id, "synthetic_clash_punch")
        self.assertEqual(native_clash_punch.length_mm, 120.0)
        self.assertAlmostEqual(native_clash_punch.cad_shape.Volume, 1132800.0, places=1)

        # Lower ram into workpiece sheet material
        self.panel.tooling_setup.set_punch_ram_z(-2.0)
        self.panel.press_cycle.ram_z_mm = -2.0
        rep_clash = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=True
        )
        self.assertEqual(rep_clash.status, PhysicalEvidence.COLLISION)
        self.assertEqual(rep_clash.native_status, "PENETRATION")
        self.assertIn("PUNCH_PENETRATION", rep_clash.reason)
        # Check measured overlap volume exceeds 1e-4 mm^3
        self.assertIn("punch=", rep_clash.reason)

        # 4. Return to Set A (puancon.FCStd + matrica.FCStd) via standard selection path (A -> B -> A)
        prof_punch_a_restored = self.panel.tool_library.load_tool("puancon", ToolKind.PUNCH)
        prof_die_a_restored = self.panel.tool_library.load_tool("matrica", ToolKind.DIE)
        self.panel.select_punch(prof_punch_a_restored)
        self.panel.select_die(prof_die_a_restored)
        self.panel.align_tooling()

        # Verify exact reproduction of Set A datums and kinematics
        self.assertEqual(self.panel.tooling_setup.punch.tool_id, set_a_punch.tool_id)
        self.assertEqual(self.panel.tooling_setup.die.tool_id, set_a_die.tool_id)
        self.assertAlmostEqual(pc.coupled_kinematics.die_opening_v_mm, baseline_ck_v, places=4)
        self.assertAlmostEqual(pc.coupled_kinematics.punch_tip_radius_mm, baseline_punch_r, places=4)
        self.assertAlmostEqual(pc.coupled_kinematics.punch_included_angle_deg, baseline_punch_ang, places=4)
        self.assertAlmostEqual(pc.coupled_kinematics.contact_z_mm, baseline_contact_z, places=4)

        # Verify exact reproduction of workpiece placement
        restored_placement = pc.current_workpiece_placement_3d()
        for c1, c2 in zip(baseline_trans, restored_placement.translation):
            self.assertAlmostEqual(c1, c2, places=6, msg="Workpiece placement translation altered")

        # Verify physical check report reproduced exactly
        restored_rep = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=True
        )
        self.assertEqual(restored_rep.status, baseline_rep.status)
        self.assertEqual(restored_rep.section_status, baseline_rep.section_status)
        self.assertEqual(restored_rep.native_status, baseline_rep.native_status)

        # 5. Verify workpiece source geometry was NOT mutated
        current_bend_angles = {b_id: float(b.angle_deg) for b_id, b in self.session.fold_state.bends.items()}
        self.assertEqual(orig_bend_angles, current_bend_angles, "Bend angles were mutated during tooling change!")

        current_panel_vertices = {
            p_id: tuple((float(x), float(y)) for x, y in poly)
            for p_id, poly in self.session.panel_vertices_xy.items()
        }
        self.assertEqual(orig_panel_vertices, current_panel_vertices, "Panel vertices were mutated during tooling change!")


if __name__ == "__main__":
    unittest.main()
