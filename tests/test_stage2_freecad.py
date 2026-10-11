import math
import sys
import os
from typing import Tuple

try:
    from adapters.freecad.freecad_backend import FreeCADBackend, HAS_FREECAD
except ImportError:
    HAS_FREECAD = False

class TestFreeCADBackend:
    def setup_method(self):
        self.backend = FreeCADBackend()

    def test_make_box_and_bbox(self):
        shape = self.backend.make_box(10.0, 20.0, 30.0)
        assert shape is not None
        
        bbox = self.backend.get_bounding_box(shape)
        assert bbox is not None
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        
        assert math.isclose(xmax - xmin, 10.0, abs_tol=0.1)
        assert math.isclose(ymax - ymin, 20.0, abs_tol=0.1)
        assert math.isclose(zmax - zmin, 30.0, abs_tol=0.1)

    def test_transform_shape(self):
        shape = self.backend.make_box(10.0, 10.0, 10.0)
        moved = self.backend.transform_shape(shape, translation=(100.0, 0.0, 0.0))
        bbox = self.backend.get_bounding_box(moved)
        assert bbox[0] >= 99.9  # xmin
        assert bbox[3] >= 109.9 # xmax

    def test_intersect_volume(self):
        box1 = self.backend.make_box(20.0, 20.0, 20.0)
        
        box2 = self.backend.transform_shape(box1, translation=(10.0, 0.0, 0.0))
        assert self.backend.intersect_volume(box1, box2) is True
        
        box3 = self.backend.transform_shape(box1, translation=(100.0, 0.0, 0.0))
        assert self.backend.intersect_volume(box1, box3) is False

    def test_extract_faces(self):
        box = self.backend.make_box(10.0, 10.0, 10.0)
        faces = self.backend.get_faces(box)
        assert len(faces) == 6
        
        extracted = self.backend.extract_faces(box, {"0", "1"})
        assert extracted is not None
        
        extracted_faces = self.backend.get_faces(extracted)
        assert len(extracted_faces) == 2

    def test_is_valid(self):
        box = self.backend.make_box(5.0, 5.0, 5.0)
        assert self.backend.is_valid(box) is True
