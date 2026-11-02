"""
test_install_workbench.py - Unit Tests for scripts/install_workbench.py.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.install_workbench import (
    REPO_ROOT,
    WB_SOURCE,
    get_default_mod_dirs,
    find_or_create_mod_dir,
    link_or_copy,
    write_marker_files,
    verify_installation,
)


class TestInstallWorkbench(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="smbs_test_install_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(str(self.tmp_dir), ignore_errors=True)

    def test_default_mod_dirs_returns_candidates(self):
        candidates = get_default_mod_dirs()
        self.assertIsInstance(candidates, list)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertIsInstance(c, Path)

    def test_find_or_create_mod_dir_custom(self):
        custom_mod = self.tmp_dir / "custom_mod"
        res = find_or_create_mod_dir(str(custom_mod))
        self.assertTrue(res.is_dir())
        self.assertEqual(res.resolve(), custom_mod.resolve())

    def test_link_or_copy_symlink_or_copy(self):
        dst = self.tmp_dir / "SMBS"
        method = link_or_copy(WB_SOURCE, dst, mode="auto")
        self.assertIn(method, ["symlink", "junction", "copy"])
        self.assertTrue(dst.exists())
        self.assertTrue((dst / "InitGui.py").exists())
        self.assertTrue((dst / "Init.py").exists())

    def test_copy_mode_with_marker_file(self):
        dst = self.tmp_dir / "SMBS_copied"
        method = link_or_copy(WB_SOURCE, dst, mode="copy")
        self.assertEqual(method, "copy")
        write_marker_files(dst)
        marker = dst / ".smbs_repo_path"
        self.assertTrue(marker.exists())
        content = marker.read_text(encoding="utf-8").strip()
        self.assertEqual(content, str(REPO_ROOT))

    def test_verify_installation_in_temp_mod(self):
        dst = self.tmp_dir / "SMBS"
        link_or_copy(WB_SOURCE, dst, mode="auto")
        write_marker_files(dst)
        ok, report = verify_installation(self.tmp_dir, ["SMBS"])
        self.assertTrue(ok)
        self.assertTrue(any("smbs_core imported" in line for line in report))
        self.assertTrue(any("toollibrary located" in line for line in report))


if __name__ == "__main__":
    unittest.main()
