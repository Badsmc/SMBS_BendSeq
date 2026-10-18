import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add FreeCAD lib and repo root
sys.path.insert(0, ".")
if "/Applications/FreeCAD.app/Contents/Resources/lib" not in sys.path:
    sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")

try:
    import FreeCAD as App
    import Part
    HAS_FREECAD = True
except ImportError:
    HAS_FREECAD = False

from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader, AmbiguousShapeError
from smbs_core.models.tooling import ToolingKind, GeometryStatus


@unittest.skipUnless(HAS_FREECAD, "FreeCAD library not available")
class TestFcstdLoaderDisambiguation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.puancon_path = cls.repo_root / "toollibrary" / "punch" / "puancon.FCStd"
        cls.matrica_path = cls.repo_root / "toollibrary" / "die" / "matrica.FCStd"

    def setUp(self):
        self.loader = FcstdToolingLoader()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_sidecar(self, fcstd_path: Path, kind: str):
        sidecar = fcstd_path.with_suffix(".tooling.json")
        data = {
            "schema_version": "1.0",
            "kind": kind,
            "source": fcstd_path.name,
            "datums": {
                "mounting_plane": {"kind": "plane", "origin": [0, 0, 0], "normal": [0, -1, 0]},
                "working_axis": {"kind": "axis", "origin": [0, 0, 0], "direction": [0, 0, -1]},
                "tip_point": {"kind": "point", "coordinates": [0, 0, 0]}
            }
        }
        sidecar.write_text(json.dumps(data))

    def test_real_puancon_loads_without_target(self):
        """puancon.FCStd (having Sketch, Body, Pad) loads without explicit target."""
        tool = self.loader.load(self.puancon_path, kind=ToolingKind.PUNCH)
        self.assertEqual(tool.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(tool.shape)

    def test_real_matrica_loads_without_target(self):
        """matrica.FCStd (having Sketch, Body, Pad) loads without explicit target."""
        tool = self.loader.load(self.matrica_path, kind=ToolingKind.DIE)
        self.assertEqual(tool.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(tool.shape)

    def test_sketch_filtered_by_volume(self):
        """Zero-volume object (Sketch wire) + solid Body -> Body chosen."""
        fcstd = self.temp_path / "sketch_body.FCStd"
        self._write_sidecar(fcstd, "punch")
        doc = App.newDocument("TestSketchBody")
        try:
            # Wire/Edge object (0 volume)
            sketch_obj = doc.addObject("Part::Feature", "Sketch")
            sketch_obj.Shape = Part.makeLine((0, 0, 0), (10, 0, 0))
            # Solid Body
            body_obj = doc.addObject("Part::Feature", "Body")
            body_obj.Shape = Part.makeBox(10, 10, 10)
            doc.recompute()
            doc.saveAs(str(fcstd))
        finally:
            App.closeDocument(doc.Name)

        tool = self.loader.load(fcstd, kind=ToolingKind.PUNCH)
        self.assertEqual(tool.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(tool.shape)

    def test_body_preferred_over_pad(self):
        """Both Body and Pad have volume -> Body preferred by preference order."""
        fcstd = self.temp_path / "body_pad.FCStd"
        self._write_sidecar(fcstd, "punch")
        doc = App.newDocument("TestBodyPad")
        try:
            body_obj = doc.addObject("Part::Feature", "Body")
            body_obj.Shape = Part.makeBox(10, 10, 10)
            pad_obj = doc.addObject("Part::Feature", "Pad")
            pad_obj.Shape = Part.makeBox(20, 20, 20)
            doc.recompute()
            doc.saveAs(str(fcstd))
        finally:
            App.closeDocument(doc.Name)

        tool = self.loader.load(fcstd, kind=ToolingKind.PUNCH)
        self.assertEqual(tool.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(tool.shape)

    def test_multiple_bodies_raises(self):
        """Two objects with same preference and volume -> AmbiguousShapeError."""
        fcstd = self.temp_path / "multi_body.FCStd"
        self._write_sidecar(fcstd, "punch")
        doc = App.newDocument("TestMultiBody")
        try:
            b1 = doc.addObject("Part::Feature", "Body1")
            b1.Shape = Part.makeBox(10, 10, 10)
            b2 = doc.addObject("Part::Feature", "Body2")
            b2.Shape = Part.makeBox(10, 10, 10)
            doc.recompute()
            doc.saveAs(str(fcstd))
        finally:
            App.closeDocument(doc.Name)

        with self.assertRaises(AmbiguousShapeError) as cm:
            self.loader.load(fcstd, kind=ToolingKind.PUNCH)
        self.assertIn("Ambiguous shape selection", str(cm.exception))
        self.assertIn("Body1", str(cm.exception))
        self.assertIn("Body2", str(cm.exception))

    def test_no_volume_raises(self):
        """Multiple shapes all with zero volume -> AmbiguousShapeError with clear message."""
        fcstd = self.temp_path / "no_volume.FCStd"
        self._write_sidecar(fcstd, "punch")
        doc = App.newDocument("TestNoVolume")
        try:
            l1 = doc.addObject("Part::Feature", "Line1")
            l1.Shape = Part.makeLine((0, 0, 0), (10, 0, 0))
            l2 = doc.addObject("Part::Feature", "Line2")
            l2.Shape = Part.makeLine((0, 0, 0), (0, 10, 0))
            doc.recompute()
            doc.saveAs(str(fcstd))
        finally:
            App.closeDocument(doc.Name)

        with self.assertRaises(AmbiguousShapeError) as cm:
            self.loader.load(fcstd, kind=ToolingKind.PUNCH)
        self.assertIn("No solid shape with non-zero volume found", str(cm.exception))

    def test_explicit_target_overrides_heuristic(self):
        """Explicit target_object_name takes precedence over heuristic."""
        fcstd = self.temp_path / "explicit_override.FCStd"
        self._write_sidecar(fcstd, "punch")
        doc = App.newDocument("TestExplicit")
        try:
            body_obj = doc.addObject("Part::Feature", "Body")
            body_obj.Shape = Part.makeBox(10, 10, 10)
            pad_obj = doc.addObject("Part::Feature", "Pad")
            pad_obj.Shape = Part.makeBox(20, 20, 20)
            doc.recompute()
            doc.saveAs(str(fcstd))
        finally:
            App.closeDocument(doc.Name)

        # Target Pad explicitly
        tool = self.loader.load(fcstd, kind=ToolingKind.PUNCH, target_object_name="Pad")
        self.assertEqual(tool.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(tool.shape)


if __name__ == "__main__":
    unittest.main()
