"""Regression tests for:
1. Temporary virtual tool length equal to active bend length (in-memory only, preserving 2D cross-section).
2. Controlled punch end-zone collision classification and CONDITIONAL continuation (never PASS).
3. GUI warning dialog confirmation vs cancellation behavior on END_ZONE_ONLY punch collisions.
4. DFS backtracking bend sequence search with subset memoization, strict-over-conditional priority,
   honest resource exhaustion (NOT_FOUND_WITHIN_SEARCH_LIMITS), and independent forward replay verification.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD
import Part
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets

from smbs_core.demo_paths import REPO_ROOT, demo_part_path
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    EndZoneCollisionPolicy,
    PunchCollisionZoneClassification,
    classify_punch_overlap_zones,
    validate_workpiece_tooling_pose,
)
from smbs_core.kinematics2d.native_tool_loader import (
    clear_native_tool_cache,
    load_native_die,
    load_native_punch,
    set_punch_ram_z,
)
from smbs_core.kinematics2d.physical_validation_25d import (
    CurrentPosePhysicalReport,
    PhysicalEvidence,
    check_current_25d_pose,
)
from smbs_core.kinematics2d.sequence_search import (
    SearchStatus,
    StepEvaluationCache,
    StepEvaluationRecord,
    search_bend_sequence_25d,
)
from smbs_core.kinematics2d.tooling_25d import ToolKind, ToolLibrary25D, ToolingSetup2D


class TestVirtualToolEndZoneAndSequenceSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.lib_dir = REPO_ROOT / "toollibrary"
        cls.punch_path = cls.lib_dir / "punch" / "PUNCH_AP_1017_88°_H104,65_R0,8.FCStd"
        cls.die_path = cls.lib_dir / "die" / "T-DIE_AT_3015_88°_V12_H80.FCStd"
        cls.punch_sha_before = hashlib.sha256(cls.punch_path.read_bytes()).hexdigest()
        cls.die_sha_before = hashlib.sha256(cls.die_path.read_bytes()).hexdigest()

    def tearDown(self):
        clear_native_tool_cache()
        self.assertEqual(
            hashlib.sha256(self.punch_path.read_bytes()).hexdigest(),
            self.punch_sha_before,
            "Source punch FCStd file on disk must never be modified!",
        )
        self.assertEqual(
            hashlib.sha256(self.die_path.read_bytes()).hexdigest(),
            self.die_sha_before,
            "Source die FCStd file on disk must never be modified!",
        )

    def test_01_virtual_tool_length_matches_active_bend_and_updates_on_bend_switch(self):
        """1. Virtual punch and die length equals active bend length and changes when switching active bend."""
        punch_100 = load_native_punch(self.punch_path, ram_z_mm=20.0, virtual_length_mm=100.0)
        die_100 = load_native_die(self.die_path, virtual_length_mm=100.0)

        self.assertTrue(punch_100.is_virtual_length)
        self.assertTrue(die_100.is_virtual_length)
        self.assertEqual(punch_100.tool_length_mode, "ACTIVE_BEND_LENGTH")
        self.assertEqual(die_100.tool_length_mode, "ACTIVE_BEND_LENGTH")
        self.assertAlmostEqual(punch_100.length_mm, 100.0, places=5)
        self.assertAlmostEqual(die_100.length_mm, 100.0, places=5)
        self.assertAlmostEqual(punch_100.source_length_mm, 100.0, places=3)
        self.assertAlmostEqual(die_100.source_length_mm, 100.0, places=3)
        self.assertAlmostEqual(punch_100.span_xm[0], -50.0, places=4)
        self.assertAlmostEqual(punch_100.span_xm[1], 50.0, places=4)

        # Switch active bend length to 50.0 mm (e.g., B03 on box.100out-50in-(ok).FCStd)
        punch_50 = load_native_punch(self.punch_path, ram_z_mm=20.0, virtual_length_mm=50.0)
        die_50 = load_native_die(self.die_path, virtual_length_mm=50.0)
        self.assertAlmostEqual(punch_50.length_mm, 50.0, places=5)
        self.assertAlmostEqual(die_50.length_mm, 50.0, places=5)
        self.assertAlmostEqual(punch_50.span_xm[0], -25.0, places=4)
        self.assertAlmostEqual(punch_50.span_xm[1], 25.0, places=4)
        self.assertAlmostEqual(die_50.span_xm[0], -25.0, places=4)
        self.assertAlmostEqual(die_50.span_xm[1], 25.0, places=4)

        # Verify set_punch_ram_z preserves virtual length metadata and span
        punch_50_moved = set_punch_ram_z(punch_50, 5.0)
        self.assertTrue(punch_50_moved.is_virtual_length)
        self.assertAlmostEqual(punch_50_moved.length_mm, 50.0, places=5)
        self.assertAlmostEqual(punch_50_moved.span_xm[0], -25.0, places=4)
        self.assertAlmostEqual(punch_50_moved.span_xm[1], 25.0, places=4)

        # Also test ToolingSetup2D helper methods
        lib = ToolLibrary25D(self.lib_dir)
        setup = ToolingSetup2D(
            punch=lib.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH),
            die=lib.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE),
        )
        setup.set_match_active_bend_length(True, active_bend_length_mm=50.0)
        self.assertAlmostEqual(setup.effective_punch_length_mm(), 50.0, places=5)
        self.assertAlmostEqual(setup.effective_die_length_mm(), 50.0, places=5)
        setup.set_match_active_bend_length(True, active_bend_length_mm=100.0)
        self.assertAlmostEqual(setup.effective_punch_length_mm(), 100.0, places=5)
        self.assertAlmostEqual(setup.effective_die_length_mm(), 100.0, places=5)

    def test_02_cross_section_profile_preserved_under_virtual_length_scaling(self):
        """2. 2D cross-section (Ym, Zm bounds, tip, V-opening, area) does not distort when virtual length changes."""
        punch_orig = load_native_punch(self.punch_path, ram_z_mm=15.0, virtual_length_mm=None)
        punch_virt = load_native_punch(self.punch_path, ram_z_mm=15.0, virtual_length_mm=42.5)
        die_orig = load_native_die(self.die_path, virtual_length_mm=None)
        die_virt = load_native_die(self.die_path, virtual_length_mm=42.5)

        bb_p_orig = punch_orig.machine_shape.BoundBox
        bb_p_virt = punch_virt.machine_shape.BoundBox
        # In machine frame: X_m is along bend axis, Y_m is transverse, Z_m is vertical
        self.assertAlmostEqual(bb_p_orig.YMin, bb_p_virt.YMin, places=5)
        self.assertAlmostEqual(bb_p_orig.YMax, bb_p_virt.YMax, places=5)
        self.assertAlmostEqual(bb_p_orig.ZMin, bb_p_virt.ZMin, places=5)
        self.assertAlmostEqual(bb_p_orig.ZMax, bb_p_virt.ZMax, places=5)
        self.assertAlmostEqual(punch_orig.tip_radius_mm, punch_virt.tip_radius_mm, places=6)

        # Volume scales strictly linearly with length because 2D cross-section is identical
        self.assertAlmostEqual(
            punch_virt.machine_shape.Volume / punch_orig.machine_shape.Volume,
            42.5 / punch_orig.length_mm,
            places=5,
        )

        bb_d_orig = die_orig.machine_shape.BoundBox
        bb_d_virt = die_virt.machine_shape.BoundBox
        self.assertAlmostEqual(bb_d_orig.YMin, bb_d_virt.YMin, places=5)
        self.assertAlmostEqual(bb_d_orig.YMax, bb_d_virt.YMax, places=5)
        self.assertAlmostEqual(bb_d_orig.ZMin, bb_d_virt.ZMin, places=5)
        self.assertAlmostEqual(bb_d_orig.ZMax, bb_d_virt.ZMax, places=5)
        self.assertAlmostEqual(
            die_virt.machine_shape.Volume / die_orig.machine_shape.Volume,
            42.5 / die_orig.length_mm,
            places=5,
        )

    def test_03_invalid_virtual_tool_construction_returns_unverified(self):
        """3. Failure to construct a valid virtual solid fails closed with UNVERIFIED."""
        with self.assertRaisesRegex(ValueError, "VIRTUAL_TOOL_CONSTRUCTION_FAILED"):
            load_native_punch(self.punch_path, ram_z_mm=10.0, virtual_length_mm=0.0)

        with self.assertRaisesRegex(ValueError, "VIRTUAL_TOOL_CONSTRUCTION_FAILED"):
            load_native_die(self.die_path, virtual_length_mm=-10.0)

        fixture = demo_part_path("box.100out-50in-(ok).FCStd")
        doc = FreeCAD.openDocument(str(fixture), True)
        try:
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, build_part_session
            unfold_obj = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
            sess = build_part_session(doc, [unfold_obj])
            panel = _PreviewPanel()
            panel.session = sess
            panel._ensure_press_cycle_controller()
            panel.align_tooling()

            with patch(
                "smbs_core.kinematics2d.native_tool_loader.load_native_punch",
                side_effect=ValueError("VIRTUAL_TOOL_CONSTRUCTION_FAILED: synthetic test error"),
            ):
                rep = check_current_25d_pose(
                    sess,
                    panel.press_cycle,
                    panel.tooling_setup,
                    native_3d=True,
                    match_active_bend_length=True,
                    virtual_tool_length_mm=50.0,
                )
                self.assertEqual(rep.status, PhysicalEvidence.UNVERIFIED)
                self.assertIn("VIRTUAL_TOOL_CONSTRUCTION_FAILED", rep.native_status)
                self.assertFalse(rep.is_fully_qualified)
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_04_to_08_end_zone_classifications_and_die_collision_invariant(self):
        """4–8. Test END_ZONE_ONLY, CENTRAL_ZONE, MIXED_CENTRAL_AND_END_ZONE, UNCLASSIFIED, and DIE collision."""
        # Create a box punch spanning Xm in [-50, +50], Ym in [-10, 10], Zm in [0, 50]
        punch_solid = Part.makeBox(100.0, 20.0, 50.0, FreeCAD.Vector(-50.0, -10.0, 0.0))
        die_solid = Part.makeBox(100.0, 40.0, 40.0, FreeCAD.Vector(-50.0, -20.0, -45.0))

        # Case 4: Overlap strictly in left end zone [-50, -35] (with end_zone_length_mm=15.0)
        wp_left_end = Part.makeBox(8.0, 6.0, 10.0, FreeCAD.Vector(-48.0, -3.0, 2.0))
        analysis_4 = classify_punch_overlap_zones(wp_left_end, punch_solid, end_zone_length_mm=15.0)
        self.assertEqual(analysis_4.classification, PunchCollisionZoneClassification.END_ZONE_ONLY)
        self.assertEqual(analysis_4.affected_sides, ("LEFT",))
        self.assertAlmostEqual(analysis_4.central_zone_overlap_volume_mm3, 0.0, places=6)
        self.assertGreater(analysis_4.end_zone_overlap_volume_mm3, 100.0)

        # Case 5: Overlap strictly in central zone [-35, +35] -> CENTRAL_ZONE and remains COLLISION (PENETRATION)
        wp_center = Part.makeBox(10.0, 6.0, 10.0, FreeCAD.Vector(-5.0, -3.0, 2.0))
        rep_center = validate_workpiece_tooling_pose(
            wp_center,
            punch_solid,
            die_solid,
            end_zone_policy=EndZoneCollisionPolicy(allow_end_zone_collision=True, end_zone_length_mm=15.0),
        )
        self.assertEqual(
            rep_center.punch_end_zone_analysis.classification,
            PunchCollisionZoneClassification.CENTRAL_ZONE,
        )
        self.assertEqual(rep_center.status, CollisionClass.PENETRATION)
        self.assertFalse(rep_center.is_safe)
        self.assertFalse(rep_center.is_conditional_safe)

        # Case 6: Mixed overlap spanning [-45, -20] across boundary -35 -> MIXED_CENTRAL_AND_END_ZONE -> PENETRATION
        wp_mixed = Part.makeBox(25.0, 6.0, 10.0, FreeCAD.Vector(-45.0, -3.0, 2.0))
        rep_mixed = validate_workpiece_tooling_pose(
            wp_mixed,
            punch_solid,
            die_solid,
            end_zone_policy=EndZoneCollisionPolicy(allow_end_zone_collision=True, end_zone_length_mm=15.0),
        )
        self.assertEqual(
            rep_mixed.punch_end_zone_analysis.classification,
            PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE,
        )
        self.assertEqual(rep_mixed.status, CollisionClass.PENETRATION)
        self.assertFalse(rep_mixed.is_safe)
        self.assertFalse(rep_mixed.is_conditional_safe)

        # Case 7: Invalid end_zone_length_mm (e.g., 2 * 60 >= 100) -> UNCLASSIFIED -> INVALID_GEOMETRY / UNVERIFIED
        rep_unclassified = validate_workpiece_tooling_pose(
            wp_left_end,
            punch_solid,
            die_solid,
            end_zone_policy=EndZoneCollisionPolicy(allow_end_zone_collision=True, end_zone_length_mm=60.0),
        )
        self.assertEqual(
            rep_unclassified.punch_end_zone_analysis.classification,
            PunchCollisionZoneClassification.UNCLASSIFIED,
        )
        self.assertEqual(rep_unclassified.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(rep_unclassified.is_safe)
        self.assertFalse(rep_unclassified.is_conditional_safe)

        # Case 8: Collision with die ALWAYS remains PENETRATION (not conditional safe) even when punch overlap is END_ZONE_ONLY
        wp_end_plus_die = wp_left_end.fuse(
            Part.makeBox(10.0, 10.0, 10.0, FreeCAD.Vector(-5.0, -5.0, -15.0))
        )
        rep_die_col = validate_workpiece_tooling_pose(
            wp_end_plus_die,
            punch_solid,
            die_solid,
            end_zone_policy=EndZoneCollisionPolicy(allow_end_zone_collision=True, end_zone_length_mm=15.0),
        )
        self.assertEqual(rep_die_col.status, CollisionClass.PENETRATION)
        self.assertFalse(rep_die_col.is_safe)
        self.assertFalse(rep_die_col.is_conditional_safe)

    def test_09_and_10_conditional_end_zone_confirmation_vs_cancellation(self):
        """9–10. END_ZONE_ONLY yields CONDITIONAL (never PASS) when allowed/confirmed, and COLLISION when cancelled."""
        fixture = demo_part_path("box.100out-50in-(ok).FCStd")
        doc = FreeCAD.openDocument(str(fixture), True)
        try:
            from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, build_part_session
            unfold_obj = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
            sess = build_part_session(doc, [unfold_obj])
            panel = _PreviewPanel()
            panel.session = sess
            panel._ensure_press_cycle_controller()
            panel.select_punch(panel.tool_library.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH))
            panel.select_die(panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE))
            panel.align_tooling()

            # Fold B01 to 90 deg, then select B02 (which has a 126.74 mm3 end-zone punch collision at s=0)
            sess.fold_state.set_angle("B01", 90.0)
            sess.select_bend_index(1)  # B02
            panel._sync_tooling_to_active_bend()

            # With 100mm punch on 95mm bend B02 at s=0 (or virtual 95mm punch shifted by -2.5mm),
            # the 2.5mm overhang at the left end collides with B01 flange (126.74 mm3 in LEFT end-zone).
            # Case 10: allow_end_zone_collision=False -> COLLISION (classified as END_ZONE_ONLY)
            rep_strict = check_current_25d_pose(
                sess,
                panel.press_cycle,
                panel.tooling_setup,
                native_3d=True,
                match_active_bend_length=False,
                allow_end_zone_collision=False,
                end_zone_length_mm=15.0,
                classify_end_zone_length_mm=15.0,
            )
            self.assertEqual(rep_strict.status, PhysicalEvidence.COLLISION)
            self.assertFalse(rep_strict.is_conditional)
            self.assertFalse(rep_strict.is_fully_qualified)
            self.assertIsNotNone(rep_strict.end_zone_analysis)
            self.assertEqual(
                rep_strict.end_zone_analysis.classification,
                PunchCollisionZoneClassification.END_ZONE_ONLY,
            )

            # Case 9: allow_end_zone_collision=True -> CONDITIONAL (never SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
            rep_cond = check_current_25d_pose(
                sess,
                panel.press_cycle,
                panel.tooling_setup,
                native_3d=True,
                match_active_bend_length=False,
                allow_end_zone_collision=True,
                end_zone_length_mm=15.0,
                classify_end_zone_length_mm=15.0,
            )
            self.assertEqual(rep_cond.status, PhysicalEvidence.CONDITIONAL)
            self.assertTrue(rep_cond.is_conditional)
            self.assertFalse(rep_cond.is_fully_qualified)
            self.assertNotEqual(rep_cond.status, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)

            # Test GUI dialog flow in _PreviewPanel.check_current_physical_pose():
            # When allow_end_zone_checkbox is unchecked and user clicks Cancel in warning dialog -> stays COLLISION
            panel.virtual_tool_len_checkbox.setChecked(False)
            panel.allow_end_zone_checkbox.setChecked(False)
            panel.end_zone_len_spin.setValue(15.0)
            with patch.object(panel, "_prompt_end_zone_conditional_dialog", return_value=False) as mock_dlg:
                panel.check_current_physical_pose()
                self.assertTrue(mock_dlg.called)
                self.assertIn("COLLISION", panel.physical_check_result.text())
                self.assertNotIn("CONDITIONAL", panel.physical_check_result.text())
                self.assertIsNone(panel._highlighted_end_zone_info)

            # When user clicks "Продолжить с предупреждением" (returns True) -> proceeds in CONDITIONAL mode
            with patch.object(panel, "_prompt_end_zone_conditional_dialog", return_value=True) as mock_dlg2:
                panel.check_current_physical_pose()
                self.assertTrue(mock_dlg2.called)
                self.assertIn("CONDITIONAL", panel.physical_check_result.text())
                self.assertIsNotNone(panel._highlighted_end_zone_info)
                self.assertEqual(panel._highlighted_end_zone_info["bend_id"], "B02")
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_11_backtracking_sequence_search_finds_reordered_sequence_and_verifies_forward_replay(self):
        """11. Backtracking search finds valid reordered sequence when initial order collides,
        prioritizes strict over conditional, reports NOT_FOUND_WITHIN_SEARCH_LIMITS on budget exhaustion,
        and runs independent forward replay verification from 0 degrees.
        """
        angles = {"B01": 0.0, "B02": 0.0, "B03": 0.0}
        bends = {
            "B01": SimpleNamespace(angle_deg=90.0, axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0))),
            "B02": SimpleNamespace(angle_deg=90.0, axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0))),
            "B03": SimpleNamespace(angle_deg=90.0, axis_xyz=((0.0, 0.0, 0.0), (50.0, 0.0, 0.0))),
        }

        class DummyFoldState:
            def __init__(self):
                self.angles = dict(angles)
                self.bends = bends
                self.transforms = {}

            def set_angle(self, b, a):
                self.angles[b] = float(a)

            def reset_all(self):
                for k in self.angles:
                    self.angles[k] = 0.0

        class DummySession:
            def __init__(self):
                self.fold_state = DummyFoldState()
                self.bend_order = ["B01", "B02", "B03"]
                self.active_bend_index = 0
                self.active_bend_id = "B01"

            def select_bend_index(self, idx):
                self.active_bend_index = int(idx)
                self.active_bend_id = self.bend_order[self.active_bend_index]

            def set_bend_order(self, order):
                self.bend_order = list(order)
                self.select_bend_index(0)

            def reset_all_bends(self):
                self.fold_state.reset_all()
                self.select_bend_index(0)

        sess = DummySession()
        press_cycle = SimpleNamespace(
            match_active_bend_length=True,
            active_bend_length_mm=100.0,
            allow_end_zone_collision=False,
            end_zone_length_mm=15.0,
            on_tooling_changed=lambda: None,
        )
        tooling_setup = SimpleNamespace(
            punch=SimpleNamespace(tool_id="P1", source_file=str(self.punch_path), length_mm=100.0),
            die=SimpleNamespace(tool_id="D1", source_file=str(self.die_path), length_mm=100.0),
            match_active_bend_length=True,
            active_bend_length_mm=100.0,
            allow_end_zone_collision=False,
            end_zone_length_mm=15.0,
        )

        # Suppose:
        # - B02 collides if B01 is already folded
        # - B03 collides if B02 is not yet folded
        # Thus initial order (B01, B02, B03) fails at B02, whereas reordered (B02, B01, B03) succeeds!
        def fake_pose_checker(session, pc, ts, **kwargs):
            active_id = session.active_bend_id
            done = {b for b, ang in session.fold_state.angles.items() if abs(ang) > 1e-6}
            if active_id == "B02" and "B01" in done:
                return CurrentPosePhysicalReport(
                    PhysicalEvidence.COLLISION, "TOUCH", "PENETRATION", "B02 blocked after B01", ("2D_SECTION",)
                )
            if active_id == "B03" and "B02" not in done:
                return CurrentPosePhysicalReport(
                    PhysicalEvidence.COLLISION, "TOUCH", "PENETRATION", "B03 blocked before B02", ("2D_SECTION",)
                )
            return CurrentPosePhysicalReport(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS, "TOUCH", "TOUCHING", "ok", ("2D_SECTION",)
            )

        def fake_traj_checker(session, ts, **kwargs):
            return CurrentPosePhysicalReport(
                PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                "NOT_APPLICABLE",
                "SAMPLED_INTERVAL_CONVERGED",
                "ok",
                ("3D_PUNCH_DIE_SAMPLED_BEND",),
            )

        report = search_bend_sequence_25d(
            sess,
            press_cycle,
            tooling_setup,
            match_active_bend_length=True,
            allow_end_zone_collision=True,
            end_zone_length_mm=15.0,
            pose_checker=fake_pose_checker,
            trajectory_checker=fake_traj_checker,
        )
        self.assertEqual(report.status, SearchStatus.STRICT_VALIDATED)
        self.assertEqual(report.overall_evidence, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
        self.assertEqual(report.found_sequence, ("B02", "B01", "B03"))
        self.assertIsNotNone(report.forward_replay)
        self.assertTrue(report.forward_replay.is_verified)

        # Now test resource exhaustion -> NOT_FOUND_WITHIN_SEARCH_LIMITS («не найдено в пределах заданного поиска»)
        sess.set_bend_order(["B01", "B02", "B03"])
        report_limit = search_bend_sequence_25d(
            sess,
            press_cycle,
            tooling_setup,
            match_active_bend_length=True,
            allow_end_zone_collision=False,
            max_checks=1,
            pose_checker=fake_pose_checker,
            trajectory_checker=fake_traj_checker,
        )
        self.assertEqual(report_limit.status, SearchStatus.NOT_FOUND_WITHIN_SEARCH_LIMITS)
        self.assertIn("не найдено в пределах заданного поиска", report_limit.message_ru)

    def test_12_step_evaluation_cache_keys_and_invalidation(self):
        """12. Cache keys include all material state parameters and invalidate when any parameter changes."""
        cache = StepEvaluationCache()
        rec_strict = StepEvaluationRecord(
            bend_id="B03",
            step_index=2,
            completed_prefix=("B01", "B02"),
            pose_status=PhysicalEvidence.COLLISION,
            trajectory_status=None,
            effective_status=PhysicalEvidence.COLLISION,
            reason="strict collision",
        )
        tooling_a = SimpleNamespace(
            punch=SimpleNamespace(tool_id="PUNCH_1", source_file="/tmp/p1.FCStd"),
            die=SimpleNamespace(tool_id="DIE_1", source_file="/tmp/d1.FCStd"),
        )
        tooling_b = SimpleNamespace(
            punch=SimpleNamespace(tool_id="PUNCH_2", source_file="/tmp/p2.FCStd"),
            die=SimpleNamespace(tool_id="DIE_1", source_file="/tmp/d1.FCStd"),
        )
        cfg_a = AdaptiveSamplingConfig(arc_segments=128, volume_tolerance_mm3=1e-4)
        cfg_b = AdaptiveSamplingConfig(arc_segments=64, volume_tolerance_mm3=1e-3)

        base_kwargs = dict(
            completed_angles={"B01": 90.0, "B02": 90.0},
            candidate_bend_id="B03",
            tooling=tooling_a,
            match_active_bend_length=True,
            effective_punch_len_mm=50.0,
            effective_die_len_mm=50.0,
            desired_midpoint_xm=0.0,
            config=cfg_a,
            allow_end_zone_collision=False,
            end_zone_length_mm=15.0,
        )
        key_base = cache.make_step_key(**base_kwargs)
        cache.put_step(key_base, rec_strict)
        self.assertIs(cache.get_step(key_base), rec_strict)

        # Changing any parameter must produce a distinct key (cache miss)
        for param, new_val in [
            ("completed_angles", {"B01": 90.0}),
            ("completed_angles", {"B01": 90.0, "B02": 45.0}),
            ("candidate_bend_id", "B04"),
            ("tooling", tooling_b),
            ("match_active_bend_length", False),
            ("effective_punch_len_mm", 100.0),
            ("effective_die_len_mm", 100.0),
            ("desired_midpoint_xm", 2.4),
            ("config", cfg_b),
            ("allow_end_zone_collision", True),
            ("end_zone_length_mm", 20.0),
        ]:
            mod_kwargs = dict(base_kwargs)
            mod_kwargs[param] = new_val
            mod_key = cache.make_step_key(**mod_kwargs)
            self.assertNotEqual(key_base, mod_key, f"Key must change when {param} changes")
            self.assertIsNone(cache.get_step(mod_key), f"Cache must miss when {param} changes")


if __name__ == "__main__":
    unittest.main()
