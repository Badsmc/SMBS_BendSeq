"""FCStd import without manual sidecars; native geometry extraction is mocked."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from freecad.bendseq.integration.fcstd_tool_import import geometry_sidecar, import_fcstd_tool
from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary, ToolingLibraryError
from freecad.bendseq.core.kinematics2d.tooling_25d import ToolKind, ValidationStatus


def profile(kind):
    return NS(kind=kind, validation_status=ValidationStatus.GEOMETRY_VERIFIED,
              validation_messages=(), length_mm=100.0, height_mm=80.0,
              limitations_25d=(),
              punch_datums=NS(source="geometry", mounting_base=((10,90),(20,90)),
                              working_tip=(15,10), included_angle_deg=30.0, tip_radius_mm=0.5),
              die_datums=NS(source="geometry", mounting_datum=((-12,-80),(12,-80)),
                            v_groove_center=(2,0), top_datum_plane_y=0.0,
                            v_opening_mm=12.0, die_angle_deg=88.0, shoulder_radius_mm=1.0))


class ImportTest(unittest.TestCase):
    def test_source_coordinates_and_no_invented_stock(self):
        for kind in (ToolKind.PUNCH, ToolKind.DIE):
            data = geometry_sidecar(profile(kind), Path("test.FCStd"))
            self.assertEqual(data["kind"], kind.value.lower())
            self.assertEqual(data["metadata"]["segments"], [])
            self.assertFalse(data["metadata"]["manufacturing_certified"])
            self.assertEqual(data["datums"]["working_axis"]["direction"], [0,0,1])
        self.assertEqual(geometry_sidecar(profile(ToolKind.PUNCH), "p.FCStd")["datums"]["working_axis"]["origin"], [15,10,0])
        die = geometry_sidecar(profile(ToolKind.DIE), "d.FCStd")
        self.assertEqual(die["datums"]["mounting_plane"]["origin"], [0,-80,0])
        self.assertEqual(die["metadata"]["v_width_mm"], 12.0)

    def test_invalid_geometry_does_not_generate_metadata(self):
        p = profile(ToolKind.PUNCH)
        p.validation_status = ValidationStatus.NEEDS_ALIGNMENT
        with self.assertRaises(ToolingLibraryError):
            geometry_sidecar(p, "p.FCStd")

    def test_missing_sidecar_is_created_only_in_library(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "library"
            for k in ("punch", "die"):
                (lib / k).mkdir(parents=True)
            manager = ToolingLibrary(lib)
            source = root / "PUNCH_30°_R0,5.FCStd"
            source.write_bytes(b"test")
            fake = NS(ActiveDocument=NS(Name="Part"), listDocuments=lambda: {"Part":object()},
                      setActiveDocument=Mock())
            with patch.dict(sys.modules, {"FreeCAD":fake}), \
                 patch("freecad.bendseq.integration.fcstd_tool_import.ToolLibrary25D") as loader, \
                 patch("freecad.bendseq.core.geometry_backend.backends.fcstd_loader.resolve_role",
                       return_value=(NS(value="punch"), "geometry", "")):
                loader.return_value._load_legacy_fcstd_tool.return_value = profile(ToolKind.PUNCH)
                desc = import_fcstd_tool(manager, source, kind="punch")
                self.assertTrue(desc.has_sidecar)
                self.assertFalse(source.with_suffix(".tooling.json").exists())
                self.assertEqual(desc.fcstd_path.read_bytes(), b"test")
                self.assertEqual(json.loads(desc.sidecar_path.read_text(encoding="utf-8"))["metadata"]["radius_mm"], 0.5)
                with self.assertRaises(ToolingLibraryError):
                    import_fcstd_tool(manager, source, kind="punch")
            fake.setActiveDocument.assert_called_with("Part")

    def test_supplied_sidecar_is_not_replaced(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "d.FCStd"
            source.touch()
            source.with_suffix(".tooling.json").write_text("original")
            manager = Mock()
            fake = NS(ActiveDocument=None)
            with patch.dict(sys.modules, {"FreeCAD":fake}), \
                 patch("freecad.bendseq.integration.fcstd_tool_import.ToolLibrary25D") as loader:
                import_fcstd_tool(manager, source, kind="die")
                loader.assert_not_called()
                manager.add_tool.assert_called_once_with(source.resolve(), kind="die")
            self.assertEqual(source.with_suffix(".tooling.json").read_text(), "original")
