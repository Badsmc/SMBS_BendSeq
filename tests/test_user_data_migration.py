"""Data preservation and resume tests using real temporary Unicode paths."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from freecad.bendseq import paths
from freecad.bendseq import user_data_migration as migration
from freecad.bendseq.integration.fcstd_session_store import tool_reference, matches_tool_reference


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="bendseq-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.old = self.root / "Old install with spaces"
        self.new = self.root / "New user data"
        self.defaults = self.root / "defaults"
        self.defaults.mkdir()
        self.source = self.old / "toollibrary" / "punch" / (chr(0x43f) + " tool.FCStd")
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"private CAD data")

    def run_migration(self):
        return migration.migrate_user_data([self.old], self.new, self.defaults)

    def test_preserves_all_extensions_and_nested_directories(self):
        for name in ("toollibrary/Clamp/a.FCStd", "toollibrary/die/b.dxf",
                     "toollibrary/punch/stock.tooling.json", "config/custom.json",
                     "sequences/recipe.json", "saved_sequences/old.json", "templates/a.svg"):
            p = self.old / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())
        report = self.run_migration()
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["files"]), 8)
        for record in report["files"]:
            self.assertEqual(migration.digest(record["source"]), migration.digest(record["destination"]))
        before = {p.relative_to(self.new): p.read_bytes() for p in self.new.rglob("*") if p.is_file()}
        self.run_migration()
        after = {p.relative_to(self.new): p.read_bytes() for p in self.new.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_conflict_preserves_both_and_mapping_survives_source_removal(self):
        target = self.new / self.source.relative_to(self.old)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"new user version")
        report = self.run_migration()
        record = report["files"][0]
        self.assertTrue(record["conflict"])
        self.assertEqual(target.read_bytes(), b"new user version")
        with patch.object(paths, "user_data", return_value=self.new):
            self.assertEqual(migration.relocated_path(str(self.source)), record["destination"])
            self.source.unlink()
            self.run_migration()
            self.assertEqual(migration.relocated_path(str(self.source)), record["destination"])

    def test_interruption_and_hash_failure_do_not_mark_complete(self):
        original = migration.digest
        def broken(path):
            return "incorrect" if Path(path).name.startswith(".copy-") else original(path)
        with patch.object(migration, "digest", side_effect=broken):
            with self.assertRaises(OSError):
                self.run_migration()
        report = json.loads((self.new / "migration-v1.json").read_text())
        self.assertFalse(report["complete"])
        self.assertTrue(report["errors"])
        self.assertTrue(self.source.is_file())
        self.assertTrue(self.run_migration()["complete"])

    def test_legacy_profile_fingerprint_requires_identical_verified_file(self):
        report = self.run_migration()
        old = SimpleNamespace(tool_id="tool", name="Punch",
                              to_dict=lambda: {"source_file": str(self.source), "angle": 85, "metrics": {}})
        reference = tool_reference(old)
        destination = report["files"][0]["destination"]
        current = SimpleNamespace(tool_id="tool", name="Punch",
                                  to_dict=lambda: {"source_file": destination, "angle": 85, "metrics": {}})
        with patch.object(paths, "user_data", return_value=self.new):
            self.assertTrue(matches_tool_reference(current, reference))
            Path(destination).write_bytes(b"different geometry")
            self.assertFalse(matches_tool_reference(current, reference))

    def test_bundled_defaults_do_not_overwrite_user_tools(self):
        seed = self.defaults / "punch" / self.source.name
        seed.parent.mkdir()
        seed.write_bytes(b"bundled sample")
        report = self.run_migration()
        self.assertEqual((self.new / self.source.relative_to(self.old)).read_bytes(), self.source.read_bytes())
        self.assertTrue(any(record["conflict"] for record in report["files"]))

    def test_platform_user_path_and_external_paths(self):
        import sys
        runtime = SimpleNamespace(getUserAppDataDir=lambda: str(self.root / "FreeCAD data"))
        with patch.dict(sys.modules, {"FreeCAD": runtime}):
            self.assertEqual(paths.tool_library(), self.root / "FreeCAD data" / "BendSeq" / "toollibrary")
        with patch.object(paths, "user_data", return_value=self.new):
            self.assertEqual(migration.relocated_path("external.FCStd"), "external.FCStd")

    def test_conflicting_model_keeps_companion_metadata_together(self):
        sidecar = self.source.with_suffix(".tooling.json")
        sidecar.write_bytes(b'{"segments": [25, 50], "counts": [2, 3]}')
        target = self.new / self.source.relative_to(self.old)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"different model")
        report = self.run_migration()
        copied = {record["source"]: Path(record["destination"]) for record in report["files"]}
        self.assertEqual(copied[str(self.source)].with_suffix(".tooling.json"), copied[str(sidecar)])
        self.assertEqual(copied[str(sidecar)].read_bytes(), sidecar.read_bytes())
