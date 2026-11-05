"""Report paths: addon-local by default, explicit override, safe fallback."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from smbs_core import report_paths


class ReportPathsTests(unittest.TestCase):
    def test_reports_under_installed_addon(self):
        with TemporaryDirectory() as root, patch.object(report_paths, "addon_root", return_value=Path(root)):
            with patch.dict(os.environ, {"SMBS_REPORTS_DIR": ""}):
                result = report_paths.reports_dir()
                self.assertEqual(result, Path(root).resolve() / "reports")
                self.assertTrue(result.is_dir())

    def test_output_inside_shared_folder(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"SMBS_REPORTS_DIR": root}):
            output = report_paths.report_file("part_overhead_123.json")
            self.assertEqual(output.parent, Path(root).resolve())

    def test_legacy_specific_output_override(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {
            "SMBS_REPORTS_DIR": root,
            "SMBS_PART_OVERHEAD_OUTPUT": str(Path(root) / "custom.json"),
        }):
            result = report_paths.report_file(
                "part_overhead_123.json", legacy_override_env="SMBS_PART_OVERHEAD_OUTPUT"
            )
            self.assertEqual(result, Path(root) / "custom.json")

    def test_invalid_filename_is_rejected(self):
        with self.assertRaises(ValueError):
            report_paths.report_file("../elsewhere.json")

    def test_fallback_when_addon_not_writable(self):
        with TemporaryDirectory() as root:
            bad_addon_root = Path(root) / "not_a_dir"
            bad_addon_root.write_text("file instead of directory", encoding="utf-8")
            fake_home = Path(root) / "home"
            fake_home.mkdir()
            with patch.object(report_paths, "addon_root", return_value=bad_addon_root), \
                    patch.object(Path, "home", return_value=fake_home), \
                    patch.dict(os.environ, {"SMBS_REPORTS_DIR": ""}):
                result = report_paths.reports_dir()
                self.assertEqual(result, (fake_home / "pressbrake_traces").resolve())


if __name__ == "__main__":
    unittest.main()
