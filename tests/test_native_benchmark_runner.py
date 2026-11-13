"""Contracts for a real FreeCAD native reference benchmark launcher.

These headless tests do not pretend to run FreeCAD Part or native SheetMetal.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.run_native_benchmark import (
    DEFAULT_PART, DEFAULT_DIE_ID, DEFAULT_PUNCH_ID,
    MANUAL_ORDER, ROOT, sha256_file, _user_tool_source,
)


class NativeBenchmarkRunnerTests(unittest.TestCase):
    def test_default_reference_inputs_are_real_repository_assets(self):
        self.assertEqual(DEFAULT_PART.name, "2-1-3-4-(ok).FCStd")
        self.assertTrue(DEFAULT_PART.is_file())
        punch = _user_tool_source(ROOT, "punch", DEFAULT_PUNCH_ID)
        die = _user_tool_source(ROOT, "die", DEFAULT_DIE_ID)
        self.assertTrue(punch.is_file())
        self.assertTrue(die.is_file())
        self.assertEqual(len(MANUAL_ORDER), 10)
        self.assertEqual(len(set(MANUAL_ORDER)), 10)

    def test_file_hash_is_real_sha256_not_a_filename_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "real.FCStd"
            p.write_bytes(b"reference native CAD bytes")
            self.assertEqual(
                sha256_file(p),
                hashlib.sha256(b"reference native CAD bytes").hexdigest(),
            )

    def test_fails_closed_for_nonexistent_selected_tool(self):
        with self.assertRaises(FileNotFoundError):
            _user_tool_source(ROOT, "punch", "NOT_A_REAL_PUNCH")

    def test_cannot_run_native_without_freecad_python(self):
        try:
            import FreeCAD  # type: ignore[import-not-found]
        except ImportError:
            from tools.run_native_benchmark import run
            with self.assertRaisesRegex(RuntimeError, "INSIDE FreeCAD"):
                run()
        else:
            self.skipTest("Real FreeCAD installed: use reference live acceptance")


if __name__ == "__main__":
    unittest.main()
