"""Pure-Python tests for the canonical in-repository FCStd demo paths."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory

from smbs_core import demo_paths


class TestDemoPaths(unittest.TestCase):
    def test_rejects_directory_traversal_and_non_fcstd(self):
        for value in ("../L-(ok).FCStd", "subdir/Z-(ok).FCStd", "test.step", ".", ".."):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    demo_paths.find_demo_part(value)

    def test_resolves_part_from_repo_demo_directory(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "parts"
            folder.mkdir()
            f = folder / "L-(ok).FCStd"
            f.write_bytes(b"test-only fixture placeholder")
            with patch.object(demo_paths, "DEMO_PARTS", folder), patch.object(demo_paths, "DEMO_ROOT", root):
                self.assertEqual(demo_paths.find_demo_part("L-(ok).FCStd"), f)

    def test_missing_model_reports_project_relative_paths(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(demo_paths, "DEMO_PARTS", root / "parts"), patch.object(demo_paths, "DEMO_ROOT", root):
                with self.assertRaisesRegex(FileNotFoundError, "parts"):
                    # The full path contains this canonical suffix.
                    demo_paths.find_demo_part("Z-(ok).FCStd")


if __name__ == "__main__":
    unittest.main()
