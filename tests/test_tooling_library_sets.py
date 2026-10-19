"""
test_tooling_library_sets.py - Unit tests for ToolingLibrary ToolingSet CRUD operations (Stage 7, S.25a).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.tooling_library import (
    ToolingLibrary,
    ToolingLibraryError,
    KIND_PUNCH,
    KIND_DIE,
)
from smbs_core.geometry_backend.io.tooling_set import ToolingSet


class TestToolingLibrarySets(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.library_dir = Path(self.temp_dir) / "lib"
        self.library_dir.mkdir()
        (self.library_dir / KIND_PUNCH).mkdir()
        (self.library_dir / KIND_DIE).mkdir()
        self.lib = ToolingLibrary(self.library_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_tool(self, kind: str, tool_id: str):
        target_dir = self.library_dir / kind
        fcstd_file = target_dir / f"{tool_id}.FCStd"
        fcstd_file.write_text("dummy FCStd content")
        sidecar_file = target_dir / f"{tool_id}.tooling.json"
        sidecar_data = {
            "schema_version": "1.0",
            "kind": kind,
            "source": f"{tool_id}.FCStd",
            "metadata": {},
        }
        sidecar_file.write_text(json.dumps(sidecar_data))

    def test_add_set_creates_file(self):
        """add_set(ToolingSet(...), check_refs=False) creates sets/<id>.json with valid content."""
        tset = ToolingSet(
            set_id="set_alpha",
            punch_id="p1",
            die_id="d1",
            clamp_positions_mm=(75.0, 275.0),
        )
        self.lib.add_set(tset, check_refs=False)
        set_file = self.library_dir / "sets" / "set_alpha.json"
        self.assertTrue(set_file.exists())

        loaded_data = json.loads(set_file.read_text(encoding="utf-8"))
        self.assertEqual(loaded_data["set_id"], "set_alpha")
        self.assertEqual(loaded_data["clamp_positions_mm"], [75.0, 275.0])

    def test_add_set_duplicate_raises(self):
        """Adding duplicate set_id raises ToolingLibraryError without overwriting."""
        tset = ToolingSet("set_dup", "p", "d", ())
        self.lib.add_set(tset, check_refs=False)
        with self.assertRaises(ToolingLibraryError) as cm:
            self.lib.add_set(tset, check_refs=False)
        self.assertIn("already exists", str(cm.exception))

    def test_get_set_returns_saved(self):
        """get_set returns saved ToolingSet identical to original."""
        orig = ToolingSet(
            set_id="set_get",
            punch_id="punch_a",
            die_id="die_b",
            clamp_positions_mm=(100.0, 300.0),
            description="Test get_set",
            metadata={"notes": "sample"},
        )
        self.lib.add_set(orig, check_refs=False)
        got = self.lib.get_set("set_get")
        self.assertEqual(got.set_id, orig.set_id)
        self.assertEqual(got.punch_id, orig.punch_id)
        self.assertEqual(got.die_id, orig.die_id)
        self.assertEqual(got.clamp_positions_mm, orig.clamp_positions_mm)
        self.assertEqual(got.description, orig.description)
        self.assertEqual(got.metadata, orig.metadata)

    def test_get_set_missing_raises(self):
        """get_set for missing set_id raises ToolingLibraryError."""
        with self.assertRaises(ToolingLibraryError) as cm:
            self.lib.get_set("nonexistent_set")
        self.assertIn("not found", str(cm.exception))

    def test_list_sets_returns_sorted_ids(self):
        """list_sets returns sorted tuple of set IDs."""
        for name in ("set_c", "set_a", "set_b"):
            self.lib.add_set(ToolingSet(name, "p", "d", ()), check_refs=False)
        self.assertEqual(self.lib.list_sets(), ("set_a", "set_b", "set_c"))

    def test_list_sets_empty_when_no_dir(self):
        """list_sets returns empty tuple when sets/ directory doesn't exist."""
        self.assertFalse((self.library_dir / "sets").exists())
        self.assertEqual(self.lib.list_sets(), ())

    def test_has_set_true_false(self):
        """has_set returns True for existing set, False otherwise."""
        self.assertFalse(self.lib.has_set("my_set"))
        self.lib.add_set(ToolingSet("my_set", "p", "d", ()), check_refs=False)
        self.assertTrue(self.lib.has_set("my_set"))
        self.assertFalse(self.lib.has_set("other_set"))

    def test_remove_set_removes_file(self):
        """remove_set deletes file and has_set becomes False."""
        self.lib.add_set(ToolingSet("to_remove", "p", "d", ()), check_refs=False)
        self.assertTrue(self.lib.has_set("to_remove"))
        self.lib.remove_set("to_remove")
        self.assertFalse(self.lib.has_set("to_remove"))
        self.assertFalse((self.library_dir / "sets" / "to_remove.json").exists())

    def test_remove_set_missing_raises(self):
        """remove_set for non-existent set raises ToolingLibraryError."""
        with self.assertRaises(ToolingLibraryError) as cm:
            self.lib.remove_set("nonexistent")
        self.assertIn("not found", str(cm.exception))

    def test_add_set_path_traversal_raises(self):
        """Path traversal characters in set_id raise ToolingLibraryError."""
        bad_ids = ["../evil", "sub/evil", "evil\\slash", ".."]
        for bad_id in bad_ids:
            with self.assertRaises(ToolingLibraryError):
                self.lib.add_set(ToolingSet(bad_id, "p", "d", ()), check_refs=False)
            with self.assertRaises(ToolingLibraryError):
                self.lib.get_set(bad_id)
            with self.assertRaises(ToolingLibraryError):
                self.lib.remove_set(bad_id)

    def test_add_set_with_missing_punch_ref_raises(self):
        """check_refs=True raises ToolingLibraryError if punch_id is missing from library."""
        self._create_dummy_tool(KIND_DIE, "real_die")
        tset = ToolingSet("set_check", "missing_punch", "real_die", ())
        with self.assertRaises(ToolingLibraryError) as cm:
            self.lib.add_set(tset, check_refs=True)
        self.assertIn("not present in library", str(cm.exception))
        self.assertIn("missing_punch", str(cm.exception))

    def test_add_set_with_check_refs_false_ok(self):
        """check_refs=False allows adding ToolingSet even when punch/die don't exist in library."""
        tset = ToolingSet("set_unvalidated", "missing_p", "missing_d", ())
        self.lib.add_set(tset, check_refs=False)
        self.assertTrue(self.lib.has_set("set_unvalidated"))

    def test_add_set_with_valid_refs_ok(self):
        """check_refs=True succeeds when both punch_id and die_id exist in library."""
        self._create_dummy_tool(KIND_PUNCH, "valid_punch")
        self._create_dummy_tool(KIND_DIE, "valid_die")
        tset = ToolingSet("set_valid", "valid_punch", "valid_die", (100.0,))
        self.lib.add_set(tset, check_refs=True)
        self.assertTrue(self.lib.has_set("set_valid"))


if __name__ == "__main__":
    unittest.main()
