import unittest
import math
from typing import Tuple

try:
    from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend, HAS_OCC
except ImportError:
    HAS_OCC = False


class TestPythonOCCBackend(unittest.TestCase):
    def setUp(self):
        if not HAS_OCC:
            self.skipTest("pythonocc-core is not installed")
        self.backend = PythonOCCBackend()

    def test_make_box_and_bbox(self):
        shape = self.backend.make_box(10.0, 20.0, 30.0)
        self.assertIsNotNone(shape)

        bbox = self.backend.get_bounding_box(shape)
        self.assertIsNotNone(bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox

        self.assertTrue(math.isclose(xmax - xmin, 10.0, abs_tol=0.1))
        self.assertTrue(math.isclose(ymax - ymin, 20.0, abs_tol=0.1))
        self.assertTrue(math.isclose(zmax - zmin, 30.0, abs_tol=0.1))

    def test_copy_shape(self):
        shape = self.backend.make_box(10.0, 10.0, 10.0)
        copied = self.backend.copy_shape(shape)
        self.assertIsNotNone(copied)

    def test_transform_shape(self):
        shape = self.backend.make_box(10.0, 10.0, 10.0)
        transformed = self.backend.transform_shape(
            shape, translation=(5.0, 0.0, 0.0), angle_deg=45.0
        )
        self.assertIsNotNone(transformed)


if __name__ == "__main__":
    unittest.main()
