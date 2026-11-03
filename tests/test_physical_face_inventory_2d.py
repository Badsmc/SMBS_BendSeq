"""Pure-Python tests for physical-face inventory; no FreeCAD installation needed."""
import unittest
from types import SimpleNamespace

from freecad_smbs.integration.physical_face_inventory import (
    inspect_physical_faces, PhysicalInventoryError,
)


def vec(x, y, z):
    return SimpleNamespace(x=x, y=y, z=z)


class Plane:
    pass


class Cylinder:
    pass


class Face:
    def __init__(self, center, normal, area=100, surface=None):
        self.Surface = surface if surface is not None else Plane()
        self.CenterOfMass = vec(*center)
        self._normal = vec(*normal)
        self.Area = area

    def normalAt(self, u, v):
        return self._normal


class Shape:
    def __init__(self, faces):
        self.Faces = faces

    def isNull(self):
        return False

    def isValid(self):
        return True


class PhysicalInventoryTests(unittest.TestCase):
    def test_opposite_sides_are_paired(self):
        shape = Shape([
            Face((0, 0, 0), (0, 0, 1)),
            Face((0, 0, 1.5), (0, 0, -1)),
            Face((0, 0, 2), (1, 0, 0), surface=Cylinder()),
        ])
        result = inspect_physical_faces(shape, 1.5)
        self.assertEqual(len(result.thickness_pairs), 1)
        self.assertEqual((result.thickness_pairs[0].face_a,
                          result.thickness_pairs[0].face_b), (1, 2))
        self.assertEqual(result.cylindrical_face_indices, (3,))
        self.assertEqual(result.unpaired_planar_indices, ())
        self.assertEqual(result.to_dict()["thickness_mm"], 1.5)

    def test_parallel_faces_are_not_opposite(self):
        shape = Shape([
            Face((0, 0, 0), (0, 0, 1)),
            Face((0, 0, 1.5), (0, 0, 1)),
        ])
        result = inspect_physical_faces(shape, 1.5)
        self.assertEqual(result.thickness_pairs, ())
        self.assertEqual(result.unpaired_planar_indices, (1, 2))

    def test_distant_faces_are_not_paired(self):
        shape = Shape([
            Face((0, 0, 0), (0, 0, 1)),
            Face((50, 0, 1.5), (0, 0, -1)),
        ])
        self.assertEqual(inspect_physical_faces(shape, 1.5).thickness_pairs, ())

    def test_invalid_thickness_fails_closed(self):
        with self.assertRaises(PhysicalInventoryError):
            inspect_physical_faces(Shape([]), 0)


if __name__ == "__main__":
    unittest.main()
