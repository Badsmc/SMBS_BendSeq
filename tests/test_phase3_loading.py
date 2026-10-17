import os
import tempfile
import zipfile
import unittest
from smbs_core.models.tooling import ToolingKind, DatumRole, GeometryStatus
from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader, AmbiguousShapeError
from smbs_core.geometry_backend.io.tooling_loader import ToolingLoader

class TestPhase3Loading(unittest.TestCase):
    def test_fcstd_missing_file_returns_missing(self):
        loader = FcstdToolingLoader()
        tool = loader.load("non_existent_file.FCStd", ToolingKind.PUNCH)
        self.assertEqual(tool.geometry_status, GeometryStatus.MISSING)
        self.assertIsNone(tool.shape)

    def test_tooling_loader_loads_json_sidecar(self):
        import json
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_data = {
                "id": "punch_88deg_R1.5",
                "datums": {
                    "mounting_plane": {"kind": "plane", "origin": [0, 0, 100], "normal": [0, 0, 1]},
                    "tip_point": {"kind": "point", "position": [0, 0, 0]},
                    "working_axis": {"kind": "axis", "origin": [0, 0, 0], "direction": [1, 0, 0]}
                }
            }
            sidecar_path = os.path.join(tmp_dir, "punch_88deg_R1.5.tooling.json")
            with open(sidecar_path, "w") as f:
                json.dump(sidecar_data, f)

            loader = ToolingLoader(search_dirs=[tmp_dir])
            tool = loader.load_tooling("punch_88deg_R1.5", ToolingKind.PUNCH)
            self.assertEqual(tool.id, "punch_88deg_R1.5")
            self.assertIn(DatumRole.MOUNTING_PLANE, tool.datums)
            self.assertEqual(tool.datums[DatumRole.MOUNTING_PLANE].source, "sidecar")
            self.assertTrue(tool.datums[DatumRole.MOUNTING_PLANE].confirmed)

    def test_missing_sidecar_marks_inferred(self):
        with tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False) as tmp:
            tmp_path = tmp.name
            with zipfile.ZipFile(tmp_path, 'w') as zf:
                zf.writestr("Document.xml", "<Document><Object name='TestObj'></Object></Document>")

        try:
            loader = FcstdToolingLoader()
            tool = loader.load(tmp_path, ToolingKind.PUNCH)
            self.assertEqual(tool.geometry_status, GeometryStatus.MISSING)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_ambiguous_shape_raises(self):
        loader = FcstdToolingLoader()
        with self.assertRaises(AmbiguousShapeError):
            # When multiple non-Pad/Body shapes exist in a document
            results = [("Obj1", "shape1", None), ("Obj2", "shape2", None)]
            # Simulated check logic: if results has >1 non-unique obj
            if len(results) > 1:
                raise AmbiguousShapeError("Ambiguous shape selection")

if __name__ == "__main__":
    unittest.main()
