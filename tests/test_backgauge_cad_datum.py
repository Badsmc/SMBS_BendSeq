"""Headless geometry-contract tests for the selected Pad.Face6 work surface.

The fixture deliberately has different source Pad and final Body face indices,
so code cannot silently replace the user-selected Pad.Face6 with Body.Face6.
FreeCAD/OpenCASCADE runtime still needs separate local validation.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.backgauge_cad_datum import (
    StopDatumError, resolve_stop_face, z_rotation_to_operator_deg,
)


class Vector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @property
    def Length(self):
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z


class Plane:
    pass


class Face:
    def __init__(self, normal, origin):
        self.Surface = Plane()
        self.ParameterRange = (0, 1, 0, 1)
        self._normal = normal
        self._origin = origin

    def normalAt(self, _u, _v):
        return self._normal

    def distToShape(self, vertex):
        return (math.dist((vertex.x, vertex.y, vertex.z),
                          (self._origin.x, self._origin.y, self._origin.z)),
                (), ())


class TestSelectedContactFace(unittest.TestCase):
    def fixture(self, *, include_final=True):
        picked = Vector(49.3567, 0.0, 6.75308)
        outward = Vector(0, -1, 0)
        displaced = Vector(25, 137.5, 7)
        pad_faces = [Face(Vector(-1, 0, 0), displaced)
                     for _ in range(5)] + [Face(outward, picked)]
        body_faces = [Face(Vector(-1, 0, 0), displaced)]
        if include_final:
            body_faces.append(Face(outward, picked))
        pad = SimpleNamespace(Shape=SimpleNamespace(
            Faces=pad_faces, isNull=lambda: False
        ))
        body = SimpleNamespace(Shape=SimpleNamespace(Faces=body_faces))
        doc = SimpleNamespace(getObject=lambda name: pad if name == "Pad" else None)
        app = SimpleNamespace(Vector=Vector)
        part = SimpleNamespace(Plane=Plane, Vertex=lambda x: x)
        datum = {
            "kind": "plane", "source_object": "Pad",
            "subelement": "Face6",
            "origin": [49.3567, 0, 6.75308], "tolerance_mm": 0.05,
        }
        return doc, body, datum, app, part

    def test_selection_maps_to_different_final_body_face(self):
        idx, picked, normal = resolve_stop_face(*self.fixture())
        self.assertEqual(idx, 2)
        self.assertAlmostEqual(picked.x, 49.3567)
        self.assertEqual(normal.dot(Vector(0, -1, 0)), 1)
        self.assertAlmostEqual(z_rotation_to_operator_deg(normal), 180)

    def test_removed_source_surface_fails_not_falls_back(self):
        with self.assertRaisesRegex(StopDatumError, "does not uniquely survive"):
            resolve_stop_face(*self.fixture(include_final=False))

    def test_wrong_selection_point_or_index_is_rejected(self):
        fixture = list(self.fixture())
        fixture[2]["origin"] = [49.3567, 30, 6.75308]
        with self.assertRaisesRegex(StopDatumError, "misses finite face"):
            resolve_stop_face(*fixture)
        fixture = list(self.fixture())
        fixture[2]["subelement"] = "Face7"
        with self.assertRaisesRegex(StopDatumError, "missing"):
            resolve_stop_face(*fixture)

    def test_source_normal_to_operator_preserves_z_up(self):
        self.assertAlmostEqual(
            z_rotation_to_operator_deg(Vector(-1, 0, 0)), -90
        )
        self.assertAlmostEqual(
            z_rotation_to_operator_deg(Vector(0, -1, 0)), 180
        )
        self.assertAlmostEqual(
            z_rotation_to_operator_deg(Vector(0, 1, 0)), 0
        )
        with self.assertRaises(StopDatumError):
            z_rotation_to_operator_deg(Vector(0, 0, 1))


if __name__ == "__main__":
    unittest.main()
