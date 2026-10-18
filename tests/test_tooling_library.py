"""
test_tooling_library.py - Unit tests for ToolingLibrary manager (Phase B, S.22e-kod).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure FreeCAD lib path is present (same pattern as test_fcstd_loader_role_resolution.py)
for _lib_p in ("/Applications/FreeCAD.app/Contents/Resources/lib", "/usr/lib/freecad/lib"):
    if os.path.exists(_lib_p) and _lib_p not in sys.path:
        sys.path.append(_lib_p)

from smbs_core.geometry_backend.io.tooling_library import (
    ToolingLibrary,
    ToolingLibraryError,
    KIND_PUNCH,
    KIND_DIE
)
from smbs_core.geometry_backend.io.tooling_catalog import ToolingCatalogError


class TestToolingLibrary(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.library_dir = Path(self.temp_dir) / "lib"
        self.library_dir.mkdir()
        (self.library_dir / KIND_PUNCH).mkdir()
        (self.library_dir / KIND_DIE).mkdir()

        self.sources_dir = Path(self.temp_dir) / "sources"
        self.sources_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_source_tool(self, name: str, sidecar_kind: str | None = "punch", metadata: dict | None = None) -> Path:
        fcstd_file = self.sources_dir / f"{name}.FCStd"
        # Copy a valid real FCStd so App.openDocument succeeds
        real_fcstd = Path("toollibrary/punch/puancon.FCStd")
        if real_fcstd.exists():
            shutil.copy2(real_fcstd, fcstd_file)
        else:
            fcstd_file.write_text("dummy")

        if sidecar_kind is not None:
            sidecar_file = self.sources_dir / f"{name}.tooling.json"
            sidecar_data = {
                "schema_version": "1.0",
                "kind": sidecar_kind,
                "source": f"{name}.FCStd",
                "metadata": metadata or {}
            }
            sidecar_file.write_text(json.dumps(sidecar_data))

        return fcstd_file

    def test_init_valid_library(self):
        """1. Valid library directory with punch and die subdirs -> OK."""
        lib = ToolingLibrary(self.library_dir)
        self.assertEqual(lib.library_root, self.library_dir.resolve())

    def test_init_missing_punch_raises(self):
        """2. Missing punch subdirectory -> ToolingLibraryError."""
        shutil.rmtree(self.library_dir / KIND_PUNCH)
        with self.assertRaises(ToolingLibraryError) as ctx:
            ToolingLibrary(self.library_dir)
        self.assertIn("Missing required punch subdirectory", str(ctx.exception))

    def test_init_missing_root_raises(self):
        """3. Root does not exist -> ToolingLibraryError."""
        nonexistent = self.library_dir / "nonexistent"
        with self.assertRaises(ToolingLibraryError) as ctx:
            ToolingLibrary(nonexistent)
        self.assertIn("does not exist", str(ctx.exception))

    def test_add_tool_with_sidecar_success(self):
        """4. Add tool with valid sidecar -> copied to punch/ and descriptor returned."""
        src_path = self._create_source_tool("test_punch", sidecar_kind="punch", metadata={"radius_mm": 0.8})
        lib = ToolingLibrary(self.library_dir)

        desc = lib.add_tool(src_path, kind="punch")
        self.assertEqual(desc.tool_id, "test_punch")
        self.assertEqual(desc.kind, KIND_PUNCH)
        self.assertTrue(desc.has_sidecar)
        self.assertEqual(desc.metadata.get("radius_mm"), 0.8)

        # Verify files in library
        dest_fcstd = self.library_dir / KIND_PUNCH / "test_punch.FCStd"
        dest_sidecar = self.library_dir / KIND_PUNCH / "test_punch.tooling.json"
        self.assertTrue(dest_fcstd.exists())
        self.assertTrue(dest_sidecar.exists())

    def test_add_tool_without_sidecar_raises(self):
        """5. Source tool without sidecar -> ToolingLibraryError."""
        src_path = self._create_source_tool("no_sidecar_tool", sidecar_kind=None)
        lib = ToolingLibrary(self.library_dir)

        with self.assertRaises(ToolingLibraryError) as ctx:
            lib.add_tool(src_path)
        self.assertIn("no sidecar", str(ctx.exception))

    def test_add_tool_kind_conflict_raises(self):
        """6. Caller kind conflict with sidecar kind -> ToolingLibraryError."""
        src_path = self._create_source_tool("conflict_tool", sidecar_kind="punch")
        lib = ToolingLibrary(self.library_dir)

        with self.assertRaises(ToolingLibraryError) as ctx:
            lib.add_tool(src_path, kind="die")
        self.assertIn("Kind conflict", str(ctx.exception))

    def test_add_tool_kind_none_resolves_via_sidecar(self):
        """7. kind=None resolves via sidecar and copies to appropriate subdir."""
        src_path = self._create_source_tool("auto_die", sidecar_kind="die")
        lib = ToolingLibrary(self.library_dir)

        desc = lib.add_tool(src_path, kind=None)
        self.assertEqual(desc.tool_id, "auto_die")
        self.assertEqual(desc.kind, KIND_DIE)
        self.assertTrue((self.library_dir / KIND_DIE / "auto_die.FCStd").exists())

    def test_add_tool_duplicate_raises(self):
        """8. Adding tool when it already exists in library -> ToolingLibraryError."""
        src_path = self._create_source_tool("dup_tool", sidecar_kind="punch")
        lib = ToolingLibrary(self.library_dir)

        lib.add_tool(src_path)
        with self.assertRaises(ToolingLibraryError) as ctx:
            lib.add_tool(src_path)
        self.assertIn("already exists", str(ctx.exception))

    def test_add_tool_path_traversal_raises(self):
        """9. tool_id with path traversal characters -> ToolingLibraryError."""
        # Create a file whose stem looks suspicious if possible, or test check logic
        evil_path = self.sources_dir / "evil..tool.FCStd"
        real_fcstd = Path("toollibrary/punch/puancon.FCStd")
        if real_fcstd.exists():
            shutil.copy2(real_fcstd, evil_path)
        else:
            evil_path.write_text("evil")
        sidecar = self.sources_dir / "evil..tool.tooling.json"
        sidecar.write_text(json.dumps({"kind": "punch"}))

        lib = ToolingLibrary(self.library_dir)
        with self.assertRaises(ToolingLibraryError) as ctx:
            lib.add_tool(evil_path)
        self.assertIn("traversal", str(ctx.exception))

    def test_remove_tool_success(self):
        """10. Remove tool -> files deleted and has_tool becomes False."""
        src_path = self._create_source_tool("removable_tool", sidecar_kind="punch")
        lib = ToolingLibrary(self.library_dir)

        lib.add_tool(src_path)
        self.assertTrue(lib.has_tool("removable_tool"))

        lib.remove_tool("removable_tool", kind="punch")
        self.assertFalse(lib.has_tool("removable_tool"))
        self.assertFalse((self.library_dir / KIND_PUNCH / "removable_tool.FCStd").exists())
        self.assertFalse((self.library_dir / KIND_PUNCH / "removable_tool.tooling.json").exists())

    def test_remove_tool_not_found_raises(self):
        """11. Removing non-existent tool -> ToolingLibraryError."""
        lib = ToolingLibrary(self.library_dir)
        with self.assertRaises(ToolingLibraryError) as ctx:
            lib.remove_tool("nonexistent", kind="punch")
        self.assertIn("not found", str(ctx.exception))

    def test_has_tool_and_get_tool_and_list_tools(self):
        """12. list_tools, get_tool, has_tool complete workflow."""
        lib = ToolingLibrary(self.library_dir)
        self.assertFalse(lib.has_tool("p1"))

        p1 = self._create_source_tool("p1", sidecar_kind="punch")
        d1 = self._create_source_tool("d1", sidecar_kind="die")

        lib.add_tool(p1)
        lib.add_tool(d1)

        self.assertTrue(lib.has_tool("p1"))
        self.assertTrue(lib.has_tool("d1"))
        self.assertFalse(lib.has_tool("other"))

        tool_p1 = lib.get_tool("p1")
        self.assertEqual(tool_p1.tool_id, "p1")
        self.assertEqual(tool_p1.kind, KIND_PUNCH)

        catalog = lib.list_tools()
        self.assertEqual(len(catalog.punches), 1)
        self.assertEqual(len(catalog.dies), 1)
        self.assertEqual(len(catalog.all_tools()), 2)


if __name__ == "__main__":
    unittest.main()
