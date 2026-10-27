"""
test_verification_dialog.py - Headless-safe tests for verification_dialog and verify command.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from freecad_smbs.ui.badges import (
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
)


class TestVerificationDialogHeadless(unittest.TestCase):
    """Verify headless safety of verification_dialog."""

    def test_verification_dialog_module_headless(self):
        """Importing verification_dialog must not import FreeCAD or PySide/PyQt."""
        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.verification_dialog; "
            "forbidden = ['FreeCAD', 'FreeCADGui', 'Part', 'PySide6', 'PySide2', 'PyQt5', 'PyQt6']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_extract_details_unverified(self):
        """When 09_verification_result is None, extract_verification_details returns UNVERIFIED."""
        from freecad_smbs.ui.verification_dialog import extract_verification_details

        mock_artifacts = MagicMock()
        mock_artifacts.run_id = "run_test_unverified"
        mock_artifacts.part_id = "test_part"
        mock_artifacts.verification_result = None

        details = extract_verification_details(mock_artifacts)
        self.assertEqual(details["status"], BADGE_UNVERIFIED)
        self.assertIsNone(details["production_eligible"])
        self.assertIn("UNVERIFIED", details["badge_text"])
        self.assertEqual(details["badge_color"], "#757575")
        self.assertIn("APPROACH", details["stages"])
        self.assertEqual(details["stages"]["APPROACH"]["status"], "UNCHECKED")

    def test_extract_details_verified(self):
        """When 09_verification_result has production_eligible=True, returns VERIFIED."""
        from freecad_smbs.ui.verification_dialog import extract_verification_details

        mock_artifacts = MagicMock()
        mock_artifacts.run_id = "run_test_verified"
        mock_artifacts.part_id = "test_part"
        mock_artifacts.verification_result = {
            "schema_name": "verification_result",
            "units": "mm",
            "diagnostics": [],
            "payload": {
                "verification_status": "VERIFIED",
                "verification_independence_level": "ISOLATED_OCC_NO_SOLVER_CACHE",
                "production_eligible": True,
                "step_verifications": [
                    {
                        "step": 1,
                        "bend_id": "B1",
                        "status": "VERIFIED",
                        "collision_verified": True,
                        "kinematics_verified": True,
                    }
                ],
                "independent_metrics": {
                    "is_valid_brep": True,
                    "solids_count": 1,
                    "shells_count": 1,
                    "volume_delta_mm3": 0.001,
                    "relative_volume_delta": 0.00001,
                    "surface_area_delta_mm2": 0.002,
                    "hausdorff_distance_mm": 0.0001,
                    "bbox_delta_mm": [0.001, 0.001, 0.001],
                },
            },
        }

        details = extract_verification_details(mock_artifacts)
        self.assertEqual(details["status"], BADGE_VERIFIED)
        self.assertTrue(details["production_eligible"])
        self.assertIn("VERIFIED", details["badge_text"])
        self.assertEqual(details["badge_color"], "#2E7D32")
        self.assertEqual(details["stages"]["APPROACH"]["status"], "PASSED")
        self.assertEqual(details["stages"]["SELF"]["status"], "PASSED")
        self.assertEqual(details["stages"]["TOOL"]["status"], "PASSED")
        self.assertEqual(details["stages"]["TRAJECTORY"]["status"], "PASSED")
        self.assertEqual(details["stages"]["RETRACT"]["status"], "PASSED")
        self.assertEqual(details["stages"]["MACHINE"]["status"], "PASSED")
        self.assertEqual(len(details["step_verifications"]), 1)
        self.assertTrue(details["metrics"]["is_valid_brep"])

    def test_extract_details_failed_with_stages(self):
        """When 09 has production_eligible=False and stage diagnostics, flags failed stage."""
        from freecad_smbs.ui.verification_dialog import extract_verification_details

        mock_artifacts = MagicMock()
        mock_artifacts.run_id = "run_test_failed"
        mock_artifacts.part_id = "test_part"
        mock_artifacts.verification_result = {
            "schema_name": "verification_result",
            "units": "mm",
            "diagnostics": [
                {
                    "code": "STEP_VALIDATION_FAILED",
                    "message": "Step 2 bend 'B2' failed: Tool collision with die (stage=TOOL)",
                    "level": "ERROR",
                    "stage": "TOOL",
                }
            ],
            "payload": {
                "verification_status": "VERIFICATION_FAILED",
                "verification_independence_level": "ISOLATED_OCC_NO_SOLVER_CACHE",
                "production_eligible": False,
                "reasons": ["Tool collision with die on bend B2"],
                "step_verifications": [
                    {
                        "step": 1,
                        "bend_id": "B1",
                        "status": "VERIFIED",
                        "collision_verified": True,
                        "kinematics_verified": True,
                    },
                    {
                        "step": 2,
                        "bend_id": "B2",
                        "status": "VERIFICATION_FAILED",
                        "collision_verified": False,
                        "kinematics_verified": False,
                    },
                ],
                "independent_metrics": {
                    "is_valid_brep": True,
                    "solids_count": 1,
                    "shells_count": 1,
                    "volume_delta_mm3": 0.05,
                    "relative_volume_delta": 0.001,
                    "surface_area_delta_mm2": 0.02,
                    "hausdorff_distance_mm": 0.01,
                    "bbox_delta_mm": [0.01, 0.01, 0.01],
                },
            },
        }

        details = extract_verification_details(mock_artifacts)
        self.assertEqual(details["status"], BADGE_VERIFICATION_FAILED)
        self.assertFalse(details["production_eligible"])
        self.assertEqual(details["stages"]["TOOL"]["status"], "FAILED")
        self.assertIn("Tool collision", details["stages"]["TOOL"]["details"])
        self.assertIn("Tool collision", details["reasons"][0])

    def test_extract_details_kl26_fail_fast(self):
        """KL-26: verification_status='VERIFIED' ∧ production_eligible=False must raise ValueError."""
        from freecad_smbs.ui.verification_dialog import extract_verification_details

        mock_artifacts = MagicMock()
        mock_artifacts.run_id = "run_kl26"
        mock_artifacts.verification_result = {
            "schema_name": "verification_result",
            "payload": {
                "verification_status": "VERIFIED",
                "production_eligible": False,  # Contradictory!
            },
        }

        with self.assertRaises(ValueError) as ctx:
            extract_verification_details(mock_artifacts)
        self.assertIn("KL-26", str(ctx.exception))


class TestVerifyCommandIntegration(unittest.TestCase):
    """Test VerifyCommand behavior."""

    @patch("freecad_smbs.commands.verify.get_current_run", return_value=None)
    @patch("freecad_smbs.commands.verify.show_error")
    def test_verify_command_no_run_shows_error(self, mock_show_error, mock_get_run):
        from freecad_smbs.commands.verify import VerifyCommand

        cmd = VerifyCommand()
        self.assertFalse(cmd.IsActive())
        cmd.Activated()
        mock_show_error.assert_called_once()
        self.assertIn("Сначала откройте", mock_show_error.call_args[0][1])

    @patch("freecad_smbs.commands.verify.get_current_run")
    def test_verify_command_is_active_when_run_loaded(self, mock_get_run):
        from freecad_smbs.commands.verify import VerifyCommand

        mock_get_run.return_value = MagicMock()
        cmd = VerifyCommand()
        self.assertTrue(cmd.IsActive())


if __name__ == "__main__":
    unittest.main()
