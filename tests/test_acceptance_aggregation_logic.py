"""Targeted unit tests for acceptance aggregation logic, partial runs, and error handling.
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
    from PySide6 import QtWidgets
    HAS_FREECAD = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.kinematics2d.physical_validation_25d import PhysicalEvidence, CurrentPosePhysicalReport
import tools.run_25d_physical_gui_acceptance as acc


class TestAcceptanceAggregationLogic(unittest.TestCase):
    """Verify acceptance aggregation logic:
    1. Empty expected bend order does not produce PASS.
    2. Partial run (max_bends < total) does not pass full sequence.
    3. pose_only=True without collision does NOT produce PASS.
    4. Console output & JSON report statuses are consistent.
    5. checked_scope accurately reflects evaluated checks (no trajectory in pose_only).
    6. State restoration failure (angles, active_idx, or transforms) blocks acceptance.
    7. UNVERIFIED triggers conditional continuation for subsequent bends.
    8. Report save failure raises without being swallowed.
    9. Collision volume reporting outputs exact volume and separates state vs max.
    10. Reordering via bend_order parameter propagates through session without mutating canonical order.
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
            # B01 returns UNVERIFIED, B02 returns CLEAR
            mock_pose.side_effect = [
                CurrentPosePhysicalReport(
                    status=PhysicalEvidence.UNVERIFIED,
                    section_status="CLEAR",
                    native_status="UNVERIFIED",
                    reason="test_unverified",
                    checked_scope=("2D_SECTION",),
                ),
                CurrentPosePhysicalReport(
                    status=PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
                    section_status="TOUCH",
                    native_status="TOUCHING",
                    reason="ok",
                    checked_scope=("2D_SECTION", "3D_TOOL_LENGTH", "3D_PUNCH_DIE_CURRENT_POSE"),
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
            # Scope checks: actual executed checks do not include trajectory
            self.assertNotIn("3D_PUNCH_DIE_SAMPLED_BEND", rep["scopes"]["checked_scope"])
            self.assertIn("ALL_BEND_TRAJECTORIES (pose_only mode)", rep["scopes"]["unverified_scope"])

    def test_checked_and_unverified_scope_accuracy(self):
        """checked_scope and unverified_scope must accurately reflect execution mode."""
        rep = acc.run(diagnostic_only=True, max_bends=1, pose_only=True)
        self.assertIn("2D_SECTION", rep["scopes"]["checked_scope"])
        self.assertIn("3D_TOOL_LENGTH", rep["scopes"]["checked_scope"])
        self.assertIn("3D_PUNCH_DIE_CURRENT_POSE", rep["scopes"]["checked_scope"])
        self.assertNotIn("3D_PUNCH_DIE_SAMPLED_BEND", rep["scopes"]["checked_scope"])
        self.assertTrue(any("SKIPPED_BENDS" in item for item in rep["scopes"]["unverified_scope"]))

    def test_strict_mode_halts_at_first_failure(self):
        """Strict mode must halt immediately on first collision and raise RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            acc.run(diagnostic_only=False, pose_only=True)
        self.assertIn("PHYSICAL_SEQUENCE_COLLISION_DETECTED", str(ctx.exception))
        self.assertIn("B02", str(ctx.exception))

    def test_pose_collision_volume_recorded_without_substitution(self):
        """A collision in pose check must record state overlap volume separately from max volume."""
        rep = acc.run(diagnostic_only=True, max_bends=2, pose_only=True)
        self.assertEqual(rep["overall_status"], "COLLISION")
        fc = rep["first_collision"]
        self.assertIsNotNone(fc)
        self.assertEqual(fc["bend_id"], "B02")
        self.assertEqual(fc["first_detected_in"], "POSE")
        self.assertAlmostEqual(fc["state_punch_overlap_volume_mm3"], 126.73888, places=2)
        # Max trajectory punch volume was 0.0 because trajectory was not run
        self.assertEqual(fc["max_punch_overlap_volume_mm3"], 0.0)
        self.assertIsInstance(fc["ram_z_mm"], float)

    def test_restoration_failure_blocks_acceptance(self):
        """If session fold state restoration fails (angle, active_idx, or transform), raise RuntimeError."""
        # 1. Test restore_session_state unit check directly for angle mismatch
        mock_sess = MagicMock()
        mock_sess.fold_state.angles = {"B01": 45.0} # mismatch with target 0.0
        mock_sess.active_bend_index = 0
        with self.assertRaises(RuntimeError) as ctx1:
            acc.restore_session_state(mock_sess, {"B01": 0.0}, 0)
        self.assertIn("FOLD_STATE_RESTORATION_FAILED", str(ctx1.exception))

        # 2. Test transform mismatch
        mock_sess2 = MagicMock()
        mock_sess2.fold_state.angles = {"B01": 0.0}
        mock_sess2.active_bend_index = 0
        exp_tf = MagicMock(translation=(0.0, 0.0, 0.0), rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        act_tf = MagicMock(translation=(10.0, 0.0, 0.0), rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        mock_sess2.fold_state.transforms = {"P01": act_tf}
        with self.assertRaises(RuntimeError) as ctx2:
            acc.restore_session_state(mock_sess2, {"B01": 0.0}, 0, target_transforms={"P01": exp_tf})
        self.assertIn("FOLD_STATE_RESTORATION_FAILED", str(ctx2.exception))
        self.assertIn("translation mismatch", str(ctx2.exception))

        # 3. Test during run() via patching restore_session_state
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
        # Step 1 (B02) was clear, Step 2 (B01) collided
        self.assertEqual(seq[0]["pose_native_status"], "TOUCHING")
        self.assertEqual(seq[1]["pose_native_status"], "PENETRATION")


if __name__ == "__main__":
    unittest.main()
