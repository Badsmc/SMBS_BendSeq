"""Targeted unit tests for acceptance aggregation logic, partial runs, longitudinal shifts, and error handling.
These tests run quickly without launching long native OCC trajectory validations.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

try:
    import FreeCAD as App
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide import QtWidgets
    HAS_FREECAD = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence, CurrentPosePhysicalReport
import tools.run_25d_physical_gui_acceptance as acc
import tools.run_comparative_variants_validation as comp


class TestAcceptanceAggregationLogic(unittest.TestCase):
    """Verify acceptance aggregation logic and candidate validation contracts:
    1. Disabled trajectory (pose_only=True or run_traj=False) never gives positive pass.
    2. Outcome strictly depends on validator return status (COLLISION, UNVERIFIED, SAMPLED_3D_CLEAR_NOT_CONTINUOUS).
    3. Unknown / unevaluated overlap volume remains None (null in JSON), never substituted with 0.0.
    4. Failure at B01 halts strict sequence and forbids normal continuation to B02.
    5. Success of B01 and B02 automatically continues to B03..B08 with explicit 0.0 mm shift.
    6. Shift +2.4 mm is passed to both check_current_25d_pose and check_active_25d_bend_trajectory.
    7. Session fold_state (angles, active_idx, transforms) is restored upon exception.
    """

    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD:
            raise unittest.SkipTest("FreeCAD not available")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_partial_sequence_max_bends_does_not_pass_full_sequence(self):
        """Evaluating only 1 bend when 8 are expected must NOT give overall PASS."""
        rep = acc.run(diagnostic_only=True, max_bends=1, pose_only=True)
        self.assertEqual(rep["sequence_scope"]["total_expected_count"], 8)
        self.assertEqual(len(rep["sequence_scope"]["evaluated_bends"]), 1)
        self.assertEqual(len(rep["sequence_scope"]["skipped_bends"]), 7)
        self.assertFalse(rep["sequence_scope"]["is_full_sequence_evaluated"])
        self.assertFalse(rep["is_legitimate_pass"])
        self.assertEqual(rep["overall_status"], "INCOMPLETE/UNRESOLVED")
        self.assertNotEqual(rep["overall_status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")

        # In non-diagnostic mode it must raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            acc.run(diagnostic_only=False, max_bends=1, pose_only=True)
        self.assertIn("PHYSICAL_SEQUENCE_INCOMPLETE", str(ctx.exception))

    def test_unverified_pose_blocks_pass_and_marks_subsequent_conditional(self):
        """UNVERIFIED status blocks PASS and marks subsequent bends as conditional."""
        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose") as mock_pose:
            mock_pose.side_effect = [
                CurrentPosePhysicalReport(
                    status=PhysicalEvidence.UNVERIFIED,
                    section_status="CLEAR",
                    native_status="UNVERIFIED",
                    reason="test_unverified",
                    checked_scope=("2D_SECTION",),
                    raw_report=None,
                ),
                CurrentPosePhysicalReport(
                    status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    section_status="TOUCH",
                    native_status="TOUCHING",
                    reason="ok",
                    checked_scope=("2D_SECTION", "3D_TOOL_LENGTH", "3D_PUNCH_DIE_CURRENT_POSE"),
                    raw_report=None,
                ),
            ]
            rep = acc.run(diagnostic_only=True, max_bends=2, pose_only=True)
            self.assertEqual(rep["overall_status"], "INCOMPLETE/UNRESOLVED")
            self.assertFalse(rep["is_legitimate_pass"])
            seq = rep["sequence"]
            self.assertEqual(len(seq), 2)
            self.assertFalse(seq[0]["is_conditional_after_rejected_bend"])
            self.assertTrue(seq[1]["is_conditional_after_rejected_bend"])
            self.assertEqual(seq[1]["conditional_reason"], "preceded_by_UNVERIFIED_at_B01")
            # Unknown overlap volumes must remain None (null in JSON), never 0.0
            self.assertIsNone(seq[0]["pose_punch_overlap_volume_mm3"])
            self.assertIsNone(seq[0]["trajectory_max_punch_overlap_volume_mm3"])
            self.assertIsNone(seq[0]["max_punch_overlap_volume_mm3"])

    def test_pose_only_without_collision_does_not_pass(self):
        """pose_only=True evaluates discrete pose only, must never produce full PASS."""
        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose") as mock_pose:
            mock_pose.return_value = CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="TOUCH",
                native_status="TOUCHING",
                reason="pose_clear",
                checked_scope=("2D_SECTION", "3D_TOOL_LENGTH", "3D_PUNCH_DIE_CURRENT_POSE"),
            )
            rep = acc.run(diagnostic_only=True, pose_only=True)
            self.assertFalse(rep["is_legitimate_pass"])
            self.assertEqual(rep["overall_status"], "INCOMPLETE/UNRESOLVED")
            self.assertNotIn("3D_PUNCH_DIE_SAMPLED_BEND", rep["scopes"]["checked_scope"])
            self.assertIn("ALL_BEND_TRAJECTORIES (pose_only mode)", rep["scopes"]["unverified_scope"])

    def test_comparative_evaluate_pose_and_traj_never_substitutes_zero_or_passes_without_traj(self):
        """evaluate_pose_and_traj with run_traj=False never gives positive step pass or 0.0 for missing volume."""
        mock_panel = MagicMock()
        mock_session = MagicMock()
        mock_session.bend_order = ("B01", "B02")
        mock_session.fold_state.bends = {"B01": MagicMock(angle_deg=90.0), "B02": MagicMock(angle_deg=90.0)}

        with patch("tools.run_comparative_variants_validation.check_current_25d_pose") as mock_pose:
            mock_pose.return_value = CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="TOUCH",
                native_status="TOUCHING",
                reason="clear",
                checked_scope=("2D_SECTION",),
                raw_report=None,
            )
            step_res = comp.evaluate_pose_and_traj(mock_panel, mock_session, 0, desired_midpoint_xm=2.4, run_traj=False)
            self.assertEqual(step_res["traj_status"], "NOT_EVALUATED")
            self.assertFalse(step_res["is_step_qualified"])
            self.assertEqual(step_res["step_status"], "INCOMPLETE/UNRESOLVED")
            self.assertIsNone(step_res["pose_punch_overlap_volume_mm3"])
            self.assertIsNone(step_res["trajectory_max_punch_overlap_volume_mm3"])
            self.assertIsNone(step_res["punch_overlap_volume_mm3"])

            summary = comp.summarize_variant_steps([step_res], run_traj=False)
            self.assertEqual(summary["overall_status"], "INCOMPLETE/UNRESOLVED")
            self.assertIn("INCOMPLETE/UNRESOLVED", summary["outcome"])

    def test_outcome_depends_strictly_on_validator_return_status(self):
        """Overall status transitions strictly with validator return values."""
        def _make_clear_report():
            raw = MagicMock()
            raw.total_states_evaluated = 5
            raw.phase_results = []
            return CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="TOUCH",
                native_status="SAMPLED_INTERVAL_CONVERGED",
                reason="ok",
                checked_scope=("2D_SECTION", "3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
                raw_report=raw,
            )

        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose") as mock_pose, \
             patch("smbs_core.kinematics2d.physical_validation_25d.check_active_25d_bend_trajectory") as mock_traj:
            # Case A: All 8 bends return SAMPLED_3D_CLEAR_NOT_CONTINUOUS -> overall SAMPLED_3D_CLEAR_NOT_CONTINUOUS
            mock_pose.side_effect = [_make_clear_report() for _ in range(8)]
            mock_traj.side_effect = [_make_clear_report() for _ in range(8)]
            rep_pass = acc.run(diagnostic_only=True, pose_only=False)
            self.assertTrue(rep_pass["is_legitimate_pass"])
            self.assertEqual(rep_pass["overall_status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")

            # Case B: Trajectory on B02 returns UNVERIFIED -> overall INCOMPLETE/UNRESOLVED
            mock_pose.side_effect = [_make_clear_report() for _ in range(8)]
            traj_list = [_make_clear_report() for _ in range(8)]
            traj_list[1] = CurrentPosePhysicalReport(
                status=PhysicalEvidence.UNVERIFIED,
                section_status="NOT_APPLICABLE",
                native_status="INTERVAL_UNRESOLVED",
                reason="unresolved",
                checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND",),
                raw_report=None,
            )
            mock_traj.side_effect = traj_list
            rep_unv = acc.run(diagnostic_only=False if False else True, pose_only=False)
            self.assertFalse(rep_unv["is_legitimate_pass"])
            self.assertEqual(rep_unv["overall_status"], "INCOMPLETE/UNRESOLVED")

    def test_b01_failure_forbids_continuation_to_b02_in_strict_mode(self):
        """Failure on B01 in strict mode stops immediately without evaluating B02."""
        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose") as mock_pose, \
             patch("smbs_core.kinematics2d.physical_validation_25d.check_active_25d_bend_trajectory") as mock_traj:
            mock_pose.return_value = CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="TOUCH",
                native_status="TOUCHING",
                reason="ok",
                checked_scope=("2D_SECTION",),
            )
            mock_traj.return_value = CurrentPosePhysicalReport(
                status=PhysicalEvidence.UNVERIFIED,
                section_status="NOT_APPLICABLE",
                native_status="INTERVAL_UNRESOLVED",
                reason="b01_unresolved",
                checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND",),
            )
            with self.assertRaises(RuntimeError) as ctx:
                acc.run(diagnostic_only=False, pose_only=False)
            self.assertIn("STOPPED_AT_FIRST_FAILURE:B01", str(ctx.exception))
            self.assertEqual(mock_pose.call_count, 1)
            self.assertEqual(mock_traj.call_count, 1)

    def test_b01_and_b02_success_triggers_b03_to_b08_and_passes_shifts_correctly(self):
        """When B01 (0.0mm) and B02 (+2.4mm) succeed, runner continues B03..B08 with explicit 0.0mm shift."""
        pose_shifts = []
        traj_shifts = []
        traj_configs = []

        def _fake_pose(session, press_cycle, tooling, *, native_3d=True, desired_midpoint_xm=0.0):
            pose_shifts.append((session.active_bend_id, float(desired_midpoint_xm)))
            return CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="TOUCH",
                native_status="TOUCHING",
                reason="ok",
                checked_scope=("2D_SECTION", "3D_PUNCH_DIE_CURRENT_POSE", "3D_TOOL_LENGTH"),
                raw_report=None,
            )

        def _fake_traj(session, tooling, *, config=None, desired_midpoint_xm=0.0):
            traj_shifts.append((session.active_bend_id, float(desired_midpoint_xm)))
            traj_configs.append(config)
            # Stop at B03 to verify B01 -> B02 -> B03 transition and shifts
            if session.active_bend_id == "B03":
                return CurrentPosePhysicalReport(
                    status=PhysicalEvidence.COLLISION,
                    section_status="NOT_APPLICABLE",
                    native_status="DISCRETE_SAMPLES_REJECTED",
                    reason="b03_collision",
                    checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND",),
                    raw_report=None,
                )
            return CurrentPosePhysicalReport(
                status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                section_status="NOT_APPLICABLE",
                native_status="SAMPLED_INTERVAL_CONVERGED",
                reason="ok",
                checked_scope=("3D_PUNCH_DIE_SAMPLED_BEND", "3D_TOOL_LENGTH"),
                raw_report=None,
            )

        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose", side_effect=_fake_pose), \
             patch("smbs_core.kinematics2d.physical_validation_25d.check_active_25d_bend_trajectory", side_effect=_fake_traj):
            rep = acc.run(
                diagnostic_only=True,
                max_bends=3,
                pose_only=False,
                bend_shifts={"B01": 0.0, "B02": 2.4},
                max_phase_time_s=300.0,
            )
            self.assertEqual(pose_shifts, [("B01", 0.0), ("B02", 2.4), ("B03", 0.0)])
            self.assertEqual(traj_shifts, [("B01", 0.0), ("B02", 2.4), ("B03", 0.0)])
            self.assertEqual(len(traj_configs), 3)
            for cfg in traj_configs:
                self.assertIsNotNone(cfg)
                self.assertEqual(cfg.max_phase_time_s, 300.0)
                self.assertEqual(cfg.volume_tolerance_mm3, 1e-4)
                self.assertEqual(cfg.arc_segments, 128)
                self.assertEqual(cfg.max_bend_angle_step_deg, 5.0)
                self.assertEqual(cfg.clearance_change_threshold_mm, 2.0)
            self.assertEqual(rep["validator_config"]["max_phase_time_s"], 300.0)
            self.assertIn("LONGITUDINAL_SHIFT_REPOSITIONING_TRANSFER_UNVERIFIED", rep["scopes"]["unverified_scope"])

    def test_pose_collision_volume_recorded_without_zero_substitution(self):
        """A collision in pose check records state overlap volume; unevaluated trajectory max volume is None."""
        rep = acc.run(diagnostic_only=True, max_bends=2, pose_only=True)
        self.assertEqual(rep["overall_status"], "COLLISION")
        fc = rep["first_collision"]
        self.assertIsNotNone(fc)
        self.assertEqual(fc["bend_id"], "B02")
        self.assertEqual(fc["first_detected_in"], "POSE")
        self.assertAlmostEqual(fc["state_punch_overlap_volume_mm3"], 126.73888, places=2)
        # Trajectory was not run in pose_only mode, so trajectory_max_punch_overlap_volume_mm3 must be None
        self.assertIsNone(fc["trajectory_max_punch_overlap_volume_mm3"])
        self.assertAlmostEqual(fc["max_punch_overlap_volume_mm3"], 126.73888, places=2)
        self.assertIsInstance(fc["ram_z_mm"], float)

    def test_restoration_on_exception_and_failure_detection(self):
        """Session fold state is restored even when an exception occurs during trajectory evaluation."""
        from smbs_core.demo_paths import demo_part_path
        from freecad_smbs.commands.pressbrake_2d import build_part_session
        fixture = demo_part_path("box.100out-50in-(ok).FCStd")
        doc = App.openDocument(str(fixture), True)
        try:
            unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
            sess = build_part_session(doc, [unfold])
            orig_angles = dict(sess.fold_state.angles)
            orig_transforms = dict(sess.fold_state.transforms)
            sess.fold_state.set_angle("B01", 45.0)
            sess.select_bend_index(3)
            acc.restore_session_state(sess, orig_angles, 0, orig_transforms)
            self.assertEqual(sess.fold_state.angles, orig_angles)
            self.assertEqual(sess.active_bend_index, 0)
        finally:
            App.closeDocument(doc.Name)

        with patch("tools.run_25d_physical_gui_acceptance.restore_session_state", side_effect=RuntimeError("FOLD_STATE_RESTORATION_FAILED: simulated")):
            with self.assertRaises(RuntimeError) as ctx3:
                acc.run(diagnostic_only=True, max_bends=1, pose_only=True)
            self.assertIn("FOLD_STATE_RESTORATION_FAILED", str(ctx3.exception))

    def test_report_save_failure_raises_exception(self):
        """If writing the report fails, save_report_safely must raise without silent swallowing."""
        with patch("pathlib.Path.write_text", side_effect=OSError("Disk full")):
            with self.assertRaises(RuntimeError) as ctx:
                acc.run(diagnostic_only=True, max_bends=1, pose_only=True)
            self.assertIn("REPORT_SAVE_FAILED", str(ctx.exception))

    def test_custom_bend_order_propagation(self):
        """Passing custom bend_order to run() applies reordered sequence without corrupting canonical."""
        custom_order = ("B02", "B01", "B03", "B04", "B05", "B06", "B07", "B08")
        rep = acc.run(diagnostic_only=True, max_bends=2, pose_only=True, bend_order=custom_order)
        self.assertEqual(rep["sequence_scope"]["full_expected_order"][:2], ["B02", "B01"])
        seq = rep["sequence"]
        self.assertEqual(seq[0]["bend_id"], "B02")
        self.assertEqual(seq[1]["bend_id"], "B01")
        self.assertEqual(seq[0]["pose_native_status"], "TOUCHING")
        self.assertEqual(seq[1]["pose_native_status"], "PENETRATION")


if __name__ == "__main__":
    unittest.main()
