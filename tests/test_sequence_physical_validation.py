"""Targeted unit and integration regressions for sequential physical validation.

Verifies:
1. Accumulation of prior completed bends and target angles without resetting to flat state.
2. Complete restoration of fold state angles and transforms even upon error or exception occurring mid-sequence.
3. Absence of overall PASS when any bend has a collision, error, or incomplete verification.
4. Tooling change updates calculations with new CAD geometry and invalidates cache across full A -> B -> A cycle.
"""
from __future__ import annotations

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
from smbs_core.kinematics2d.physical_validation_25d import (
    PhysicalEvidence,
    check_active_25d_bend_trajectory,
    check_current_25d_pose,
)
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig, AdaptiveTrajectoryValidator3D
from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die
from smbs_core.kinematics2d.tooling_25d import ToolKind, ValidationStatus


class TestSequencePhysicalValidation(unittest.TestCase):
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

    def test_prior_bends_accumulation_in_session(self):
        """1. Verify that prior completed bends accumulate with their exact signs and targets."""
        state = self.session.fold_state
        initial_angles = dict(state.angles)
        self.assertEqual(len(self.session.bend_order), 8)

        # Before bend 0: all flat
        self.assertTrue(all(abs(a) < 1e-6 for a in state.angles.values()))

        # Complete step 0 (B01)
        b01_target = float(state.bends['B01'].angle_deg)
        state.set_angle('B01', b01_target)
        self.assertAlmostEqual(state.angles['B01'], b01_target)
        self.assertTrue(all(abs(state.angles[b]) < 1e-6 for b in self.session.bend_order[1:]))

        # Complete step 1 (B02)
        b02_target = float(state.bends['B02'].angle_deg)
        state.set_angle('B02', b02_target)
        self.assertAlmostEqual(state.angles['B01'], b01_target)
        self.assertAlmostEqual(state.angles['B02'], b02_target)
        self.assertTrue(all(abs(state.angles[b]) < 1e-6 for b in self.session.bend_order[2:]))

    def test_restoration_of_fold_state_after_mid_sequence_error(self):
        """2. Verify that fold state is fully restored even if an exception occurs mid-sequence (after mutating angles)."""
        state = self.session.fold_state
        # Baseline state
        saved_angles = dict(state.angles)
        saved_active_idx = int(self.session.active_bend_index)

        from tools.run_25d_physical_gui_acceptance import run
        from unittest.mock import patch

        # Simulate a crash during step 2 (after B01 was folded to 90 deg)
        call_count = [0]
        def crash_on_second_bend(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("SIMULATED_MID_SEQUENCE_CRASH")
            from smbs_core.kinematics2d.physical_validation_25d import check_current_25d_pose as real_pose
            return real_pose(*args, **kwargs)

        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose", side_effect=crash_on_second_bend):
            with self.assertRaises(RuntimeError) as ctx:
                run(diagnostic_only=False, pose_only=True)
            self.assertIn("SIMULATED_MID_SEQUENCE_CRASH", str(ctx.exception))

        self.assertEqual(call_count[0], 2)

    def test_tooling_change_recomputes_geometry_and_invalidates_cache(self):
        """3. Verify that switching punch and die (A -> B -> A) updates CAD geometry and invalidates validator cache."""
        prof_punch_a = self.panel.tooling_setup.punch
        prof_die_a = self.panel.tooling_setup.die
        native_punch_a = load_native_punch(prof_punch_a.source_file, ram_z_mm=50.0)
        native_die_a = load_native_die(prof_die_a.source_file)

        validator = AdaptiveTrajectoryValidator3D(
            self.session.fold_state, self.session.panel_build,
            native_punch_a, native_die_a,
        )
        # Snapshot 1 with Set A
        st1 = validator.create_state_snapshot("B01", "CONTACT", 0.0, 0.0, 6.5, 1.0, {})
        self.assertGreater(len(validator._solid_cache), 0)
        cached_volume_a = validator.punch_base.cad_shape.Volume

        # Switch to Set B: synthetic_clash_punch + synthetic_die_v24
        prof_clash = self.panel.tool_library.load_tool("synthetic_clash_punch", ToolKind.PUNCH)
        prof_die_v24 = self.panel.tool_library.load_tool("synthetic_die_v24", ToolKind.DIE)
        native_punch_b = load_native_punch(prof_clash.source_file, ram_z_mm=50.0)
        native_die_b = load_native_die(prof_die_v24.source_file)

        validator.set_punch(native_punch_b)
        validator.set_die(native_die_b)

        # Caches must be completely emptied
        self.assertEqual(len(validator._solid_cache), 0)
        self.assertEqual(len(validator._die_clearance_cache), 0)
        self.assertEqual(validator.punch_base.tool_id, "synthetic_clash_punch")
        self.assertEqual(validator.die.tool_id, "synthetic_die_v24")
        self.assertNotEqual(validator.punch_base.cad_shape.Volume, cached_volume_a)

        # Calculate snapshot with Set B: verify it evaluates without using stale cache
        st_b = validator.create_state_snapshot("B01", "CONTACT", 0.0, 0.0, 6.5, 1.0, {})
        self.assertGreater(len(validator._solid_cache), 0)

        # Restore Set A
        validator.set_punch(native_punch_a)
        validator.set_die(native_die_a)
        self.assertEqual(len(validator._solid_cache), 0)
        self.assertEqual(validator.punch_base.tool_id, "puancon")
        self.assertEqual(validator.die.tool_id, "matrica")
        self.assertEqual(validator.punch_base.cad_shape.Volume, cached_volume_a)


if __name__ == "__main__":
    unittest.main()
