"""
test_tooling_catalog.py - Unit tests for ToolingCatalog (Phase B, S.22b-kod).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.tooling_catalog import (
    ToolingCatalog,
    ToolingCatalogError,
    ToolDescriptor,
    scan_library,
    KIND_PUNCH,
    KIND_DIE
)


class TestToolingCatalog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.library_dir = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_real_library(self):
        """1. Scan real toollibrary in repository."""
        repo_library = Path("toollibrary")
        self.assertTrue(repo_library.exists(), "toollibrary directory must exist in repo root")

        catalog = scan_library(repo_library)
        self.assertIsInstance(catalog, ToolingCatalog)
        self.assertEqual(len(catalog.punches), 1)
        self.assertEqual(len(catalog.dies), 1)

        punch = catalog.punches[0]
        self.assertEqual(punch.tool_id, "puancon")
        self.assertEqual(punch.kind, KIND_PUNCH)
        self.assertTrue(punch.has_sidecar)
        self.assertIn("radius_mm", punch.metadata)
        self.assertEqual(punch.metadata["radius_mm"], 1.0)

        die = catalog.dies[0]
        self.assertEqual(die.tool_id, "matrica")
        self.assertEqual(die.kind, KIND_DIE)
        self.assertTrue(die.has_sidecar)
        self.assertIn("v_width_mm", die.metadata)
        self.assertEqual(die.metadata["v_width_mm"], 12)

    def test_real_library_get_tool(self):
        """2. get_tool on real library."""
        catalog = scan_library(Path("toollibrary"))
        punch = catalog.get_tool("puancon")
        self.assertEqual(punch.kind, KIND_PUNCH)
        self.assertEqual(punch.tool_id, "puancon")

        die = catalog.get_tool("matrica")
        self.assertEqual(die.kind, KIND_DIE)
        self.assertEqual(die.tool_id, "matrica")

    def test_missing_library_root_raises(self):
        """3. Path does not exist -> ToolingCatalogError."""
        nonexistent = self.library_dir / "does_not_exist"
        with self.assertRaises(ToolingCatalogError) as ctx:
            scan_library(nonexistent)
        self.assertIn("does not exist", str(ctx.exception))

    def test_missing_punch_subdir_raises(self):
        """4. Missing punch/ directory -> ToolingCatalogError."""
        (self.library_dir / "die").mkdir()
        with self.assertRaises(ToolingCatalogError) as ctx:
            scan_library(self.library_dir)
        self.assertIn("Missing required punch subdirectory", str(ctx.exception))

    def test_empty_punch_and_die_dirs(self):
        """5. Empty punch and die dirs -> OK with empty tuples."""
        (self.library_dir / "punch").mkdir()
        (self.library_dir / "die").mkdir()

        catalog = scan_library(self.library_dir)
        self.assertEqual(catalog.punches, ())
        self.assertEqual(catalog.dies, ())
        self.assertEqual(catalog.all_tools(), ())
        self.assertEqual(catalog.list_tools(KIND_PUNCH), ())
        self.assertEqual(catalog.list_tools(KIND_DIE), ())

    def test_tool_without_sidecar(self):
        """6. Tool without sidecar -> has_sidecar=False, metadata={}."""
        punch_dir = self.library_dir / "punch"
        die_dir = self.library_dir / "die"
        punch_dir.mkdir()
        die_dir.mkdir()

        (punch_dir / "simple_punch.FCStd").write_text("dummy")

        catalog = scan_library(self.library_dir)
        self.assertEqual(len(catalog.punches), 1)
        tool = catalog.punches[0]
        self.assertEqual(tool.tool_id, "simple_punch")
        self.assertEqual(tool.kind, KIND_PUNCH)
        self.assertFalse(tool.has_sidecar)
        self.assertIsNone(tool.sidecar_path)
        self.assertEqual(tool.metadata, {})

    def test_malformed_sidecar_raises(self):
        """7. Malformed JSON sidecar -> ToolingCatalogError with cause."""
        punch_dir = self.library_dir / "punch"
        die_dir = self.library_dir / "die"
        punch_dir.mkdir()
        die_dir.mkdir()

        (punch_dir / "bad_tool.FCStd").write_text("dummy")
        (punch_dir / "bad_tool.tooling.json").write_text("{ unclosed json: ")

        with self.assertRaises(ToolingCatalogError) as ctx:
            scan_library(self.library_dir)
        self.assertIn("Malformed sidecar JSON", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, json.JSONDecodeError)

    def test_sidecar_kind_mismatch_raises(self):
        """8. Sidecar specifies kind mismatch -> ToolingCatalogError."""
        punch_dir = self.library_dir / "punch"
        die_dir = self.library_dir / "die"
        punch_dir.mkdir()
        die_dir.mkdir()

        (punch_dir / "mismatch.FCStd").write_text("dummy")
        (punch_dir / "mismatch.tooling.json").write_text(json.dumps({
            "schema_version": "1.0",
            "kind": "die",  # Mismatch! placed in punch/
            "source": "mismatch.FCStd"
        }))

        with self.assertRaises(ToolingCatalogError) as ctx:
            scan_library(self.library_dir)
        self.assertIn("Sidecar kind mismatch", str(ctx.exception))

    def test_two_fcstd_in_same_dir_no_conflict(self):
        """9. Multiple distinct tools in same kind -> both indexed."""
        punch_dir = self.library_dir / "punch"
        die_dir = self.library_dir / "die"
        punch_dir.mkdir()
        die_dir.mkdir()

        (punch_dir / "p1.FCStd").write_text("dummy")
        (punch_dir / "p2.FCStd").write_text("dummy")

        catalog = scan_library(self.library_dir)
        self.assertEqual(len(catalog.punches), 2)
        tool_ids = {p.tool_id for p in catalog.punches}
        self.assertEqual(tool_ids, {"p1", "p2"})

    def test_get_tool_not_found_raises(self):
        """10. get_tool with non-existent id -> ToolingCatalogError."""
        (self.library_dir / "punch").mkdir()
        (self.library_dir / "die").mkdir()

        catalog = scan_library(self.library_dir)
        with self.assertRaises(ToolingCatalogError) as ctx:
            catalog.get_tool("missing_id")
        self.assertIn("not found", str(ctx.exception))

    def test_all_tools_sorted(self):
        """11. all_tools sorted by (kind, tool_id). Ambiguity handling verified."""
        punch_dir = self.library_dir / "punch"
        die_dir = self.library_dir / "die"
        punch_dir.mkdir()
        die_dir.mkdir()

        (punch_dir / "z_punch.FCStd").write_text("dummy")
        (punch_dir / "a_punch.FCStd").write_text("dummy")
        (die_dir / "z_die.FCStd").write_text("dummy")
        (die_dir / "a_die.FCStd").write_text("dummy")

        catalog = scan_library(self.library_dir)
        all_t = catalog.all_tools()
        self.assertEqual(len(all_t), 4)

        # Dies sort first alphabetically by kind ('die' < 'punch')
        order = [(t.kind, t.tool_id) for t in all_t]
        expected_order = [
            ("die", "a_die"),
            ("die", "z_die"),
            ("punch", "a_punch"),
            ("punch", "z_punch"),
        ]
        self.assertEqual(order, expected_order)

        # Check unknown kind error
        with self.assertRaises(ToolingCatalogError):
            catalog.list_tools("invalid_kind")


if __name__ == "__main__":
    unittest.main()
