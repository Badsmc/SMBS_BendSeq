"""
test_fcstd_loader_role_resolution.py - Unit tests for Q1=D role resolution in FcstdToolingLoader (Phase B, S.22c-kod).
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

# Add FreeCAD lib path if not already present
for p in ("/Applications/FreeCAD.app/Contents/Resources/lib", "/usr/lib/freecad/lib"):
    if os.path.exists(p) and p not in sys.path:
        sys.path.append(p)

try:
    import FreeCAD as App  # type: ignore
    import Part  # type: ignore
    HAS_FREECAD = True
except ImportError:
    HAS_FREECAD = False

from smbs_core.models.tooling import ToolingKind, GeometryStatus
from smbs_core.geometry_backend.backends.fcstd_loader import (
    FcstdToolingLoader,
    resolve_role,
    RoleResolutionSource,
    RoleResolutionError
)


@unittest.skipUnless(HAS_FREECAD, "FreeCAD Python API required for role resolution tests")
class TestRoleResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.fixtures_dir = Path(cls.temp_dir)

        # 1. FCStd with SMBS_Role="punch" property on object
        cls.prop_punch_path = cls.fixtures_dir / "prop_punch.FCStd"
        doc = App.newDocument("DocPropPunch")
        box = doc.addObject("Part::Box", "ToolBody")
        box.addProperty("App::PropertyString", "SMBS_Role", "SMBS", "Role")
        box.SMBS_Role = "punch"
        doc.recompute()
        doc.saveAs(str(cls.prop_punch_path))
        App.closeDocument(doc.Name)

        # 2. FCStd with target object property
        cls.multi_obj_path = cls.fixtures_dir / "multi_obj.FCStd"
        doc = App.newDocument("DocMultiObj")
        b1 = doc.addObject("Part::Box", "UnusedBody")
        b2 = doc.addObject("Part::Box", "TargetBody")
        b2.addProperty("App::PropertyString", "SMBS_Role", "SMBS", "Role")
        b2.SMBS_Role = "die"
        doc.recompute()
        doc.saveAs(str(cls.multi_obj_path))
        App.closeDocument(doc.Name)

        # 3. FCStd with invalid property
        cls.invalid_prop_path = cls.fixtures_dir / "invalid_prop.FCStd"
        doc = App.newDocument("DocInvalidProp")
        box = doc.addObject("Part::Box", "ToolBody")
        box.addProperty("App::PropertyString", "SMBS_Role", "SMBS", "Role")
        box.SMBS_Role = "banana"
        doc.recompute()
        doc.saveAs(str(cls.invalid_prop_path))
        App.closeDocument(doc.Name)

        # 4. FCStd with Name="PunchBody" (no property)
        cls.name_punch_path = cls.fixtures_dir / "name_punch.FCStd"
        doc = App.newDocument("DocNamePunch")
        doc.addObject("Part::Box", "PunchBody")
        doc.recompute()
        doc.saveAs(str(cls.name_punch_path))
        App.closeDocument(doc.Name)

        # 5. FCStd with Name="Matrica" (no property)
        cls.name_matrica_path = cls.fixtures_dir / "name_matrica.FCStd"
        doc = App.newDocument("DocNameMatrica")
        doc.addObject("Part::Box", "Matrica_V12")
        doc.recompute()
        doc.saveAs(str(cls.name_matrica_path))
        App.closeDocument(doc.Name)

        # 6. FCStd with Name="Stanina" (no property)
        cls.name_frame_path = cls.fixtures_dir / "name_frame.FCStd"
        doc = App.newDocument("DocNameFrame")
        doc.addObject("Part::Box", "StaninaFrame")
        doc.recompute()
        doc.saveAs(str(cls.name_frame_path))
        App.closeDocument(doc.Name)

        # 7. FCStd with generic names "Pad", "Body" (no property)
        cls.generic_path = cls.fixtures_dir / "generic_tool.FCStd"
        doc = App.newDocument("DocGeneric")
        doc.addObject("Part::Box", "Body")
        doc.recompute()
        doc.saveAs(str(cls.generic_path))
        App.closeDocument(doc.Name)

        # 8. FCStd with PunchBody name BUT SMBS_Role="die" property
        cls.override_path = cls.fixtures_dir / "override_prop_over_name.FCStd"
        doc = App.newDocument("DocOverride")
        b = doc.addObject("Part::Box", "PunchBody")
        b.addProperty("App::PropertyString", "SMBS_Role", "SMBS", "Role")
        b.SMBS_Role = "die"
        doc.recompute()
        doc.saveAs(str(cls.override_path))
        App.closeDocument(doc.Name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_resolve_role_via_property(self):
        """1. Property SMBS_Role='punch' -> (PUNCH, PROPERTY)."""
        kind, source, msg = resolve_role(self.prop_punch_path)
        self.assertEqual(kind, ToolingKind.PUNCH)
        self.assertEqual(source, RoleResolutionSource.PROPERTY)
        self.assertIn("property SMBS_Role", msg)

    def test_resolve_role_via_property_target_object(self):
        """2. Property on specific target_object_name."""
        kind, source, msg = resolve_role(self.multi_obj_path, explicit_target_object_name="TargetBody")
        self.assertEqual(kind, ToolingKind.DIE)
        self.assertEqual(source, RoleResolutionSource.PROPERTY)

    def test_invalid_property_value_raises(self):
        """3. Invalid property value 'banana' -> RoleResolutionError."""
        with self.assertRaises(RoleResolutionError) as ctx:
            resolve_role(self.invalid_prop_path)
        self.assertIn("Invalid SMBS_Role='banana'", str(ctx.exception))

    def test_resolve_role_via_name_punch(self):
        """4. Name contains 'Punch' -> (PUNCH, NAME)."""
        kind, source, msg = resolve_role(self.name_punch_path)
        self.assertEqual(kind, ToolingKind.PUNCH)
        self.assertEqual(source, RoleResolutionSource.NAME)
        self.assertIn("punch", msg)

    def test_resolve_role_via_name_die_matrica(self):
        """5. Name contains 'Matrica' -> (DIE, NAME)."""
        kind, source, msg = resolve_role(self.name_matrica_path)
        self.assertEqual(kind, ToolingKind.DIE)
        self.assertEqual(source, RoleResolutionSource.NAME)
        self.assertIn("matrica", msg)

    def test_resolve_role_via_name_frame(self):
        """6. Name contains 'Stanina' -> (MACHINE_FRAME, NAME)."""
        kind, source, msg = resolve_role(self.name_frame_path)
        self.assertEqual(kind, ToolingKind.MACHINE_FRAME)
        self.assertEqual(source, RoleResolutionSource.NAME)
        self.assertIn("machine_frame", msg)

    def test_resolve_role_via_sidecar(self):
        """7. Generic object name, but sidecar says 'punch' -> (PUNCH, SIDECAR)."""
        sidecar_data = {"schema_version": "1.0", "kind": "punch"}
        kind, source, msg = resolve_role(self.generic_path, sidecar_data=sidecar_data)
        self.assertEqual(kind, ToolingKind.PUNCH)
        self.assertEqual(source, RoleResolutionSource.SIDECAR)
        self.assertIn("sidecar", msg)

    def test_resolve_role_invalid_sidecar_kind_raises(self):
        """8. Invalid sidecar kind -> RoleResolutionError."""
        sidecar_data = {"kind": "unsupported_kind"}
        with self.assertRaises(RoleResolutionError):
            resolve_role(self.generic_path, sidecar_data=sidecar_data)

    def test_property_wins_over_name(self):
        """9. Object named 'PunchBody' has property SMBS_Role='die' -> property wins."""
        kind, source, msg = resolve_role(self.override_path)
        self.assertEqual(kind, ToolingKind.DIE)
        self.assertEqual(source, RoleResolutionSource.PROPERTY)

    def test_name_wins_over_sidecar(self):
        """10. Object named 'PunchBody' with sidecar kind='die' -> name wins (Level 2 > Level 3)."""
        sidecar_data = {"kind": "die"}
        kind, source, msg = resolve_role(self.name_punch_path, sidecar_data=sidecar_data)
        self.assertEqual(kind, ToolingKind.PUNCH)
        self.assertEqual(source, RoleResolutionSource.NAME)

    def test_load_with_kind_none_resolves(self):
        """11. FcstdToolingLoader.load() with kind=None automatically resolves role."""
        loader = FcstdToolingLoader()
        tool = loader.load(self.name_punch_path, kind=None)
        self.assertEqual(tool.kind, ToolingKind.PUNCH)
        self.assertEqual(tool.smbs_role, "punch")
        self.assertEqual(tool.smbs_role_source, "name")
        self.assertEqual(tool.geometry_status, GeometryStatus.SIMPLIFIED)  # no sidecar

    def test_load_with_explicit_kind_skips_resolution(self):
        """12. FcstdToolingLoader.load() with explicit kind keeps smbs_role_source=None (legacy)."""
        loader = FcstdToolingLoader()
        tool = loader.load(self.name_punch_path, kind=ToolingKind.DIE)
        self.assertEqual(tool.kind, ToolingKind.DIE)
        self.assertEqual(tool.smbs_role, "die")
        self.assertIsNone(tool.smbs_role_source)

    def test_load_unresolved_returns_missing(self):
        """13. Generic name and no sidecar -> kind=None returns MISSING tool."""
        loader = FcstdToolingLoader()
        tool = loader.load(self.generic_path, kind=None)
        self.assertEqual(tool.geometry_status, GeometryStatus.MISSING)
        self.assertIsNone(tool.smbs_role)
        self.assertIsNone(tool.smbs_role_source)
        self.assertTrue(any("Role could not be resolved" in d for d in tool.diagnostics))


    def test_malformed_fcstd_raises_resolution_error(self):
        """14. Corrupted .FCStd (not a valid ZIP/doc) -> RoleResolutionError, not silent pass to sidecar."""
        corrupt_path = self.fixtures_dir / "corrupt.FCStd"
        corrupt_path.write_bytes(b"this is not a valid FCStd document at all")

        # Also place a sidecar that would resolve to "punch" if silently fallen through
        sidecar_path = self.fixtures_dir / "corrupt.tooling.json"
        sidecar_path.write_text(json.dumps({"kind": "punch"}))

        try:
            with self.assertRaises(RoleResolutionError) as ctx:
                resolve_role(corrupt_path)
            self.assertIn("FreeCAD failed to inspect", str(ctx.exception))
            self.assertIn("corrupt.FCStd", str(ctx.exception))
        finally:
            if sidecar_path.exists():
                sidecar_path.unlink()


if __name__ == "__main__":
    unittest.main()
