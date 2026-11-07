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
    """Verify that acceptance logic properly handles:
    - max_bends=1 or partial sequences do NOT confirm full sequence
    - empty bend order does NOT produce PASS
    - UNVERIFIED pose blocks overall PASS
    - strict mode halts on first failure
    - specific state collision volume is recorded and not confused with maximums
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
        self.assertEqual(rep["overall_status"], "INCOMPLETE/UNRESOLVED")
        self.assertNotEqual(rep["overall_status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")

        # In non-diagnostic mode it must raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            acc.run(diagnostic_only=False, max_bends=1, pose_only=True)
        self.assertIn("PHYSICAL_SEQUENCE_INCOMPLETE", str(ctx.exception))

    def test_unverified_pose_blocks_pass(self):
        """UNVERIFIED status at initial stationary pose must block overall PASS."""
        with patch("smbs_core.kinematics2d.physical_validation_25d.check_current_25d_pose") as mock_pose:
            mock_pose.return_value = CurrentPosePhysicalReport(
                status=PhysicalEvidence.UNVERIFIED,
                section_status="CLEAR",
                native_status="UNVERIFIED",
                reason="test_unverified",
                checked_scope=(),
            )
            rep = acc.run(diagnostic_only=True, max_bends=1, pose_only=True)
            self.assertEqual(rep["overall_status"], "INCOMPLETE/UNRESOLVED")
            self.assertNotEqual(rep["overall_status"], "SAMPLED_3D_CLEAR_NOT_CONTINUOUS")

    def test_strict_mode_halts_at_first_failure(self):
        """Strict mode must halt immediately on first collision and save partial report."""
        with self.assertRaises(RuntimeError) as ctx:
            acc.run(diagnostic_only=False, pose_only=True)
        self.assertIn("PHYSICAL_SEQUENCE_COLLISION_DETECTED", str(ctx.exception))
        self.assertIn("B02", str(ctx.exception))

    def test_pose_collision_volume_recorded(self):
        """A collision in pose check must record state overlap volume and not fail with format errors."""
        rep = acc.run(diagnostic_only=True, max_bends=2, pose_only=True)
        self.assertEqual(rep["overall_status"], "COLLISION")
        fc = rep["first_collision"]
        self.assertIsNotNone(fc)
        self.assertEqual(fc["bend_id"], "B02")
        self.assertEqual(fc["first_detected_in"], "POSE")
        self.assertAlmostEqual(fc["state_punch_overlap_volume_mm3"], 126.73888, places=2)
        self.assertIsInstance(fc["ram_z_mm"], float)


if __name__ == "__main__":
    unittest.main()
