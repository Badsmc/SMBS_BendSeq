"""Pure Python cross-platform smoke tests for canonical FreeCAD demo paths."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core import demo_paths
from smbs_core.geometry_backend.freecad_bootstrap import get_sheetmetal_mod_candidates


class DemoPathTests(unittest.TestCase):
    def test_repo_root_is_package_relative(self):
        expected = Path(demo_paths.__file__).resolve().parent.parent
        self.assertEqual(demo_paths.REPO_ROOT, expected)
        self.assertEqual(demo_paths.DEMO_PARTS_DIR, expected / "demo" / "parts")

    def test_refuses_absolute_and_traversal_filenames_on_all_hosts(self):
        for filename in ("../L-(ok).FCStd", r"C:\\tmp\\L-(ok).FCStd",
                         "Z/part.FCStd", r"..\\part.FCStd",
                         "/tmp/L-(ok).FCStd", "file.step", ""):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    demo_paths.demo_part_path(filename)

    def test_missing_model_fails_closed_and_names_destination(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "demo" / "parts"
            with patch.object(demo_paths, "DEMO_PARTS_DIR", folder):
                with self.assertRaisesRegex(FileNotFoundError, "demo"):
                    demo_paths.demo_part_path("L-(ok).FCStd")
                self.assertEqual(demo_paths.all_demo_parts(), ())

    def test_loads_real_file_without_modifying_contents(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "demo" / "parts"
            folder.mkdir(parents=True)
            file_path = folder / "L-(ok).FCStd"
            file_path.write_bytes(b"fixture bytes for path-only tests")
            with patch.object(demo_paths, "DEMO_PARTS_DIR", folder):
                self.assertEqual(demo_paths.demo_part_path(file_path.name), file_path)
                self.assertEqual(demo_paths.all_demo_parts(), (file_path,))
                self.assertEqual(file_path.read_bytes(), b"fixture bytes for path-only tests")

    def test_versioned_windows_macos_linux_sheetmetal_locations(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "user"
            roaming = Path(td) / "roaming"
            xdg = Path(td) / "xdg"
            with patch.dict("os.environ", {
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(Path(td) / "local"),
                "XDG_DATA_HOME": str(xdg),
            }), patch("pathlib.Path.home", return_value=home):
                candidates = get_sheetmetal_mod_candidates()
            self.assertIn(str(roaming / "FreeCAD" / "v1-1" / "Mod" / "sheetmetal"), candidates)
            self.assertIn(str(home / "Library" / "Application Support" / "FreeCAD" /
                              "v1-1" / "Mod" / "sheetmetal"), candidates)
            self.assertIn(str(xdg / "FreeCAD" / "v1-1" / "Mod" / "sheetmetal"), candidates)


if __name__ == "__main__":
    unittest.main()
