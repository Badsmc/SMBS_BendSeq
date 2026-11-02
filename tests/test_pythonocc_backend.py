import unittest

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend, HAS_OCC


class TestPythonOCCBackendQueries(unittest.TestCase):
    def setUp(self):
        if not HAS_OCC:
            self.skipTest("pythonocc-core is not installed")
        self.backend = PythonOCCBackend()

    def test_a3_ext_get_surface_area_raises_on_null_shape(self):
        """A3-ext inv#3: null shape must raise, not return 0.0."""
        with self.assertRaisesRegex(ValueError, "shape is None or null"):
            self.backend.get_surface_area(None)

    def test_a3_get_face_center_raises_on_null_face(self):
        """A3 inv#3: null face must raise, not return (0,0,0)."""
        with self.assertRaisesRegex(ValueError, "face is None or null"):
            self.backend.get_face_center(None)

    def test_a3_ext_get_surface_area_raises_on_degenerate(self):
        """A3-ext: zero-area shape must raise, not fabricate."""
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCC.Core.gp import gp_Pnt
        degenerate = BRepBuilderAPI_MakeVertex(gp_Pnt(0.0, 0.0, 0.0)).Shape()
        with self.assertRaisesRegex(ValueError, "degenerate"):
            self.backend.get_surface_area(degenerate)

    def test_a3_get_face_center_real_face_works(self):
        """A3: valid face returns non-zero center (regression guard)."""
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE
        box = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(10.0, 10.0, 10.0)).Shape()
        exp = TopExp_Explorer(box, TopAbs_FACE)
        face = exp.Current()
        cx, cy, cz = self.backend.get_face_center(face)
        self.assertGreater(abs(cx) + abs(cy) + abs(cz), 0.0)


if __name__ == "__main__":
    unittest.main()
