"""
test_tooling_set.py - Unit tests for ToolingSet model and loader/saver (Stage 7, S.25a).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.tooling_set import (
    ToolingSet,
    ToolingSetError,
    validate_set_dict,
    load_set_file,
    save_set_file,
    DEFAULT_SET_SCHEMA_VERSION,
)


class TestToolingSet(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_from_dict_valid(self):
        """Minimal dict builds ToolingSet with correct fields."""
        data = {
            "schema_version": "1.0",
            "set_id": "set_1",
            "punch_id": "punch_std",
            "die_id": "die_v12",
            "clamp_positions_mm": [275.0, 75.0],
        }
        tset = ToolingSet.from_dict(data)
        self.assertEqual(tset.set_id, "set_1")
        self.assertEqual(tset.punch_id, "punch_std")
        self.assertEqual(tset.die_id, "die_v12")
        self.assertEqual(tset.clamp_positions_mm, (75.0, 275.0))  # sorted
        self.assertEqual(tset.description, "")
        self.assertEqual(tset.metadata, {})
        self.assertEqual(tset.schema_version, DEFAULT_SET_SCHEMA_VERSION)

    def test_from_dict_with_optional_fields(self):
        """from_dict with description and metadata populates all fields."""
        data = {
            "schema_version": "1.0",
            "set_id": "set_u",
            "punch_id": "punch_goose",
            "die_id": "die_v8",
            "clamp_positions_mm": [100.0],
            "description": "Gooseneck for U-profile",
            "metadata": {"author": "engineer1", "version": 2},
        }
        tset = ToolingSet.from_dict(data)
        self.assertEqual(tset.description, "Gooseneck for U-profile")
        self.assertEqual(tset.metadata, {"author": "engineer1", "version": 2})

    def test_from_dict_missing_required_raises(self):
        """Missing required fields raises ToolingSetError."""
        data = {
            "schema_version": "1.0",
            "set_id": "set_1",
            "die_id": "die_v12",
            "clamp_positions_mm": [],
        }
        with self.assertRaises(ToolingSetError):
            ToolingSet.from_dict(data)

    def test_from_dict_wrong_schema_version_raises(self):
        """Unsupported schema_version raises ToolingSetError."""
        data = {
            "schema_version": "2.0",
            "set_id": "set_1",
            "punch_id": "p",
            "die_id": "d",
            "clamp_positions_mm": [],
        }
        with self.assertRaises(ToolingSetError):
            ToolingSet.from_dict(data)

    def test_from_dict_invalid_positions_raises(self):
        """Non-numeric positions raise ToolingSetError."""
        data = {
            "schema_version": "1.0",
            "set_id": "set_1",
            "punch_id": "p",
            "die_id": "d",
            "clamp_positions_mm": ["abc"],
        }
        with self.assertRaises(ToolingSetError):
            ToolingSet.from_dict(data)

    def test_to_dict_roundtrip(self):
        """ToolingSet -> to_dict -> from_dict is identical."""
        orig = ToolingSet(
            set_id="my_set",
            punch_id="p_1",
            die_id="d_1",
            clamp_positions_mm=(475.0, 75.0, 275.0),
            description="A test set",
            metadata={"tag": "special"},
        )
        d = orig.to_dict()
        restored = ToolingSet.from_dict(d)
        self.assertEqual(orig.set_id, restored.set_id)
        self.assertEqual(orig.punch_id, restored.punch_id)
        self.assertEqual(orig.die_id, restored.die_id)
        self.assertEqual(restored.clamp_positions_mm, (75.0, 275.0, 475.0))
        self.assertEqual(orig.description, restored.description)
        self.assertEqual(orig.metadata, restored.metadata)

    def test_to_dict_minimal_omits_empty_fields(self):
        """Empty description and metadata omitted from to_dict."""
        tset = ToolingSet(
            set_id="min_set",
            punch_id="p",
            die_id="d",
            clamp_positions_mm=(),
        )
        d = tset.to_dict()
        self.assertNotIn("description", d)
        self.assertNotIn("metadata", d)
        self.assertEqual(d["clamp_positions_mm"], [])

    def test_validate_set_dict_schema_violation_raises(self):
        """Schema violation (additional properties or invalid type) raises ToolingSetError."""
        data = {
            "schema_version": "1.0",
            "set_id": "set_1",
            "punch_id": "p",
            "die_id": "d",
            "clamp_positions_mm": [],
            "unexpected_property": True,
        }
        with self.assertRaises(ToolingSetError):
            validate_set_dict(data)

    def test_validate_set_dict_valid(self):
        """Valid dict passes schema validation and returns ToolingSet."""
        data = {
            "schema_version": "1.0",
            "set_id": "valid_set",
            "punch_id": "punch_1",
            "die_id": "die_1",
            "clamp_positions_mm": [75.0, 275.0],
            "description": "Valid set",
        }
        tset = validate_set_dict(data)
        self.assertEqual(tset.set_id, "valid_set")
        self.assertEqual(tset.clamp_positions_mm, (75.0, 275.0))

    def test_load_save_set_file_roundtrip(self):
        """Saving and loading ToolingSet file preserves all data."""
        file_path = Path(self.temp_dir) / "sub" / "test_set.json"
        tset = ToolingSet(
            set_id="saved_set",
            punch_id="puancon",
            die_id="matrica",
            clamp_positions_mm=(100.0, 300.0),
            description="Saved set test",
            metadata={"created_by": "test"},
        )
        save_set_file(file_path, tset)
        self.assertTrue(file_path.exists())

        loaded = load_set_file(file_path)
        self.assertEqual(loaded.set_id, tset.set_id)
        self.assertEqual(loaded.punch_id, tset.punch_id)
        self.assertEqual(loaded.die_id, tset.die_id)
        self.assertEqual(loaded.clamp_positions_mm, tset.clamp_positions_mm)
        self.assertEqual(loaded.description, tset.description)
        self.assertEqual(loaded.metadata, tset.metadata)

    def test_load_set_file_malformed_json_raises(self):
        """Malformed JSON file raises ToolingSetError."""
        bad_file = Path(self.temp_dir) / "bad.json"
        bad_file.write_text("MALFORMED { JSON", encoding="utf-8")
        with self.assertRaises(ToolingSetError):
            load_set_file(bad_file)


    def test_save_set_file_raises_when_schema_missing(self):
        """save_set_file must raise ToolingSetError if schema file is missing (no silent skip)."""
        from unittest.mock import patch
        from smbs_core.geometry_backend.io import tooling_set as ts_module

        set_obj = ToolingSet(set_id="x", punch_id="p", die_id="d", clamp_positions_mm=())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sets" / "x.json"

            original_exists = Path.exists
            def fake_exists(self_path):
                if "tooling_set.schema.json" in str(self_path):
                    return False
                return original_exists(self_path)

            with patch.object(Path, "exists", fake_exists):
                with self.assertRaises(ts_module.ToolingSetError) as ctx:
                    ts_module.save_set_file(out, set_obj)
                self.assertIn("schema not found", str(ctx.exception).lower())

    def test_save_set_file_raises_when_schema_malformed(self):
        """save_set_file wraps malformed schema JSON in ToolingSetError (not uncaught JSONDecodeError)."""
        from unittest.mock import patch
        from smbs_core.geometry_backend.io import tooling_set as ts_module

        set_obj = ToolingSet(set_id="x", punch_id="p", die_id="d", clamp_positions_mm=())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sets" / "x.json"

            original_load = json.load
            def fake_load(f, *args, **kwargs):
                if "schema" in getattr(f, "name", ""):
                    raise json.JSONDecodeError("Malformed schema", "", 0)
                return original_load(f, *args, **kwargs)

            with patch("smbs_core.geometry_backend.io.tooling_set.json.load", side_effect=fake_load):
                with self.assertRaises(ts_module.ToolingSetError) as ctx:
                    ts_module.save_set_file(out, set_obj)
                self.assertIn("schema validation", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
