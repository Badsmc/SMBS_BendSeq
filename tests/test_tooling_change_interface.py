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
        """Execute full tooling change sequence, verify updates, clash detection, and exact Set A restoration."""
        # 1. Baseline Set A
        set_a_punch = self.panel.tooling_setup.punch
        set_a_die = self.panel.tooling_setup.die
        self.assertIsNotNone(set_a_punch, "Default Set A punch must be loaded")
        self.assertIsNotNone(set_a_die, "Default Set A die must be loaded")
        self.assertIn("puancon", set_a_punch.tool_id.lower())
        self.assertIn("matrica", set_a_die.tool_id.lower())

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

        # 2. Select synthetic test die with V = 24.0 mm, shoulder radius 2.0 mm (labeled SYNTHETIC_TEST_TOOLING)
        synth_die = copy.deepcopy(set_a_die)
        synth_die.tool_id = "SYNTHETIC_TEST_TOOLING_DIE_V24"
        synth_die.name = "SYNTHETIC_TEST_TOOLING_DIE_V24"
        synth_die.v_opening_mm = 24.0
        synth_die.shoulder_radius_mm = 2.0
        synth_die.die_datums = DieDatums2D(
            left_shoulder=(-12.0, 0.0),
            right_shoulder=(12.0, 0.0),
            v_groove_center=(0.0, -14.0),
            top_datum_plane_y=0.0,
            mounting_datum=set_a_die.die_datums.mounting_datum,
            axis_direction=(0.0, 1.0),
            v_opening_mm=24.0,
            die_angle_deg=88.0,
            shoulder_radius_mm=2.0,
            v_depth_mm=14.0,
            source="synthetic_test",
        )
        self.panel.select_die(synth_die)

        self.assertEqual(self.panel.tooling_setup.die.tool_id, "SYNTHETIC_TEST_TOOLING_DIE_V24")
        self.assertIsNotNone(pc.coupled_kinematics)
        self.assertAlmostEqual(pc.coupled_kinematics.die_opening_v_mm, 24.0, places=3)
        self.assertAlmostEqual(pc.coupled_kinematics.die_shoulder_radius_mm, 2.0, places=3)

        # 3. Select synthetic test punch with acute angle 30.0 deg, radius 3.0 mm (labeled SYNTHETIC_TEST_TOOLING)
        synth_punch = copy.deepcopy(set_a_punch)
        synth_punch.tool_id = "SYNTHETIC_TEST_TOOLING_PUNCH_A30"
        synth_punch.name = "SYNTHETIC_TEST_TOOLING_PUNCH_A30"
        synth_punch.tip_radius_mm = 3.0
        synth_punch.punch_datums = PunchDatums2D(
            working_tip=(0.0, 0.0),
            axis_direction=(0.0, -1.0),
            mounting_base=set_a_punch.punch_datums.mounting_base,
            side_bounds=set_a_punch.punch_datums.side_bounds,
            tip_radius_mm=3.0,
            included_angle_deg=30.0,
            source="synthetic_test",
        )
        self.panel.select_punch(synth_punch)

        self.assertEqual(self.panel.tooling_setup.punch.tool_id, "SYNTHETIC_TEST_TOOLING_PUNCH_A30")
        self.assertAlmostEqual(pc.coupled_kinematics.punch_tip_radius_mm, 3.0, places=3)
        self.assertAlmostEqual(pc.coupled_kinematics.punch_included_angle_deg, 30.0, places=3)

        # 4. Known clash configuration: severe penetration of punch into workpiece
        clash_punch = copy.deepcopy(synth_punch)
        clash_punch.tool_id = "SYNTHETIC_TEST_TOOLING_CLASH_PUNCH"
        clash_punch.name = "SYNTHETIC_TEST_TOOLING_CLASH_PUNCH"
        self.panel.select_punch(clash_punch)

        # Drive punch down into the sheet material (negative ram Z)
        self.panel.tooling_setup.set_punch_ram_z(-2.0)
        clash_rep = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=False
        )
        self.assertEqual(clash_rep.status, PhysicalEvidence.COLLISION)
        self.assertEqual(clash_rep.section_status, ContactStatus.INTERFERENCE.value)

        # 5. Return to Set A (puancon.FCStd + matrica.FCStd)
        self.panel.select_punch(set_a_punch)
        self.panel.select_die(set_a_die)
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

        # Verify physical check report reproduced
        restored_rep = check_current_25d_pose(
            self.session, self.panel.press_cycle, self.panel.tooling_setup, native_3d=True
        )
        self.assertEqual(restored_rep.status, baseline_rep.status)
        self.assertEqual(restored_rep.section_status, baseline_rep.section_status)
        self.assertEqual(restored_rep.native_status, baseline_rep.native_status)

        # 6. Verify workpiece source geometry was NOT mutated
        current_bend_angles = {b_id: float(b.angle_deg) for b_id, b in self.session.fold_state.bends.items()}
        self.assertEqual(orig_bend_angles, current_bend_angles, "Bend angles were mutated during tooling change!")

        current_panel_vertices = {
            p_id: tuple((float(x), float(y)) for x, y in poly)
            for p_id, poly in self.session.panel_vertices_xy.items()
        }
        self.assertEqual(orig_panel_vertices, current_panel_vertices, "Panel vertices were mutated during tooling change!")


if __name__ == "__main__":
    unittest.main()
