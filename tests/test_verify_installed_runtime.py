"""Verify FreeCAD Mod source comparison does not require FreeCAD or writes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smbs_core.kinematics2d.runtime_build_identity import NATIVE_SOURCE_PATHS
from tools.verify_installed_runtime import compare_source_trees


def copy_test_sources(root: Path):
    for name in NATIVE_SOURCE_PATHS:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(name, encoding="utf-8")


class ModSourceVerifierTests(unittest.TestCase):
    def test_exact_copy_matches_without_git(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "A", Path(td) / "FreeCADMod"
            copy_test_sources(a)
            copy_test_sources(b)
            report = compare_source_trees(a, b)
            self.assertTrue(report["match"])
            self.assertEqual(report["changed_or_missing_files"], [])
            self.assertEqual(report["reference_sha256"],
                             report["installed_sha256"])

    def test_older_native_collision_module_must_fail_version_gate(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "A", Path(td) / "Mod"
            copy_test_sources(a)
            copy_test_sources(b)
            old = b / "smbs_core/kinematics2d/native_collision_3d.py"
            old.write_text("OLD_NATIVE_OCC_RECOMPUTES_COMMON", encoding="utf-8")
            report = compare_source_trees(a, b)
            self.assertFalse(report["match"])
            self.assertEqual(report["changed_or_missing_files"],
                             ["smbs_core/kinematics2d/native_collision_3d.py"])

    def test_missing_cache_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "A", Path(td) / "Mod"
            copy_test_sources(a)
            copy_test_sources(b)
            name = NATIVE_SOURCE_PATHS[-1]
            (b / name).unlink()
            report = compare_source_trees(a, b)
            self.assertFalse(report["match"])
            self.assertIn(name, report["installed_missing_files"])


if __name__ == "__main__":
    unittest.main()
