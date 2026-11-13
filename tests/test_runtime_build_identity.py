"""Runtime identity contracts for real FreeCAD ZIP installs lacking .git."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from smbs_core.kinematics2d.runtime_build_identity import (
    NATIVE_SOURCE_PATHS,
    RUNTIME_FINGERPRINT_SCHEMA,
    runtime_source_fingerprint,
)


class InstalledSourceFingerprintTests(unittest.TestCase):
    def test_composite_fingerprint_is_stable_and_changes_on_one_file_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for rel in NATIVE_SOURCE_PATHS:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rel, encoding="utf-8")
            old = runtime_source_fingerprint(root)
            same = runtime_source_fingerprint(root)
            self.assertEqual(old, same)
            self.assertEqual(old["schema_version"], RUNTIME_FINGERPRINT_SCHEMA)
            self.assertTrue(old["required_source_files_present"])
            self.assertEqual(old["missing_required_files"], [])
            self.assertEqual(len(old["installed_source_sha256"]),
                             len(NATIVE_SOURCE_PATHS))
            self.assertEqual(len(old["composite_source_sha256"]), 64)
            changed_path = root / NATIVE_SOURCE_PATHS[2]
            changed_path.write_text("CHANGED", encoding="utf-8")
            changed = runtime_source_fingerprint(root)
            self.assertNotEqual(old["composite_source_sha256"],
                                changed["composite_source_sha256"])
            for name in NATIVE_SOURCE_PATHS:
                self.assertEqual(
                    old["installed_source_sha256"][name] ==
                    changed["installed_source_sha256"][name],
                    name != NATIVE_SOURCE_PATHS[2],
                )

    def test_missing_module_yields_no_fabricated_complete_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            result = runtime_source_fingerprint(temp)
            self.assertFalse(result["required_source_files_present"])
            self.assertIsNone(result["composite_source_sha256"])
            self.assertEqual(set(result["missing_required_files"]),
                             set(NATIVE_SOURCE_PATHS))
            self.assertTrue(all(v is None for v in
                                result["installed_source_sha256"].values()))

    def test_repo_files_resolve_to_fingerprint_without_freecad_or_git(self):
        root = Path(__file__).resolve().parents[1]
        result = runtime_source_fingerprint(root)
        self.assertTrue(result["required_source_files_present"])
        p = root / NATIVE_SOURCE_PATHS[2]
        self.assertEqual(
            result["installed_source_sha256"][NATIVE_SOURCE_PATHS[2]],
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
