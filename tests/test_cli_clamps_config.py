"""
test_cli_clamps_config.py - Unit tests for CLI --clamps-config flag (S.24c).
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.cli import main
from smbs_core.geometry_backend.io.clamp_validator import ClampConfig


class TestCliClampsConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.model_file = Path(self.temp_dir) / "test_model.FCStd"
        self.model_file.write_text("dummy model content")

        self.valid_clamps_file = Path(self.temp_dir) / "valid_clamps.json"
        valid_cfg = ClampConfig.from_defaults((75.0, 275.0))
        self.valid_clamps_file.write_text(json.dumps(valid_cfg.to_dict()), encoding="utf-8")

        self.output_file = Path(self.temp_dir) / "sequence.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _mock_coordinator_success(self, *args, **kwargs):
        # Create dummy export file
        export_file = Path(self.temp_dir) / "export.json"
        export_file.write_text(json.dumps({"dummy": True}))
        return {
            "status": "SUCCESS",
            "planning_status": "SUCCESS",
            "verification_status": "VERIFIED",
            "production_eligible": True,
            "export_file_path": str(export_file),
        }

    @patch("smbs_core.cli.Coordinator")
    def test_cli_with_valid_clamps_config(self, mock_coord_cls):
        """--clamps-config with valid file passes validation and prints honest notice to stderr."""
        mock_instance = mock_coord_cls.return_value
        mock_instance.start_run.side_effect = self._mock_coordinator_success

        cmd = [
            str(self.model_file),
            "--clamps-config", str(self.valid_clamps_file),
            "--output", str(self.output_file),
        ]

        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

        err_output = stderr_buf.getvalue()
        self.assertIn("Clamps config loaded", err_output)
        self.assertIn("validated, not enforced — enforcement is requirement 4", err_output)

        # Verify machine_config was passed with clamps_config object
        call_kwargs = mock_instance.start_run.call_args[1]
        m_cfg = call_kwargs.get("machine_config")
        self.assertIsNotNone(m_cfg)
        self.assertIn("clamps_config", m_cfg)
        self.assertEqual(m_cfg["clamps_config"].positions_mm, (75.0, 275.0))

    def test_cli_with_missing_clamps_config_file(self):
        """--clamps-config with non-existent file exits code 2 with error."""
        nonexistent = Path(self.temp_dir) / "no_such_file.json"
        cmd = [
            str(self.model_file),
            "--clamps-config", str(nonexistent),
            "--output", str(self.output_file),
        ]

        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        err_output = stderr_buf.getvalue()
        self.assertIn("Error: clamps config file not found", err_output)

    def test_cli_with_malformed_clamps_json(self):
        """--clamps-config with malformed JSON exits code 2 with error."""
        bad_json = Path(self.temp_dir) / "bad.json"
        bad_json.write_text("INVALID { JSON")
        cmd = [
            str(self.model_file),
            "--clamps-config", str(bad_json),
            "--output", str(self.output_file),
        ]

        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        err_output = stderr_buf.getvalue()
        self.assertIn("Error: invalid clamps config", err_output)

    def test_cli_with_invalid_schema(self):
        """--clamps-config with invalid schema (e.g. negative width) exits code 2 with error."""
        bad_schema = Path(self.temp_dir) / "bad_schema.json"
        bad_schema.write_text(json.dumps({"schema_version": "1.0", "clamps": {"width_mm": -10.0}}))
        cmd = [
            str(self.model_file),
            "--clamps-config", str(bad_schema),
            "--output", str(self.output_file),
        ]

        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", ["smbs-cli"] + cmd), patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 2)

        err_output = stderr_buf.getvalue()
        self.assertIn("Error: invalid clamps config", err_output)

    @patch("smbs_core.cli.Coordinator")
    def test_cli_without_clamps_config_still_works(self, mock_coord_cls):
        """Running CLI without --clamps-config works as before without regressions."""
        mock_instance = mock_coord_cls.return_value
        mock_instance.start_run.side_effect = self._mock_coordinator_success

        cmd = [
            str(self.model_file),
            "--output", str(self.output_file),
        ]

        with patch.object(sys, "argv", ["smbs-cli"] + cmd):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

        call_kwargs = mock_instance.start_run.call_args[1]
        self.assertIsNone(call_kwargs.get("machine_config"))


if __name__ == "__main__":
    unittest.main()
