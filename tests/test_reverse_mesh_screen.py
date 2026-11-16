"""Pure-Python checks for SAT mesh narrowphase ranking.

No FreeCAD dependency: fake Part.Face tessellate provides triangle geometry.
"""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.reverse_mesh_screen import (
    approximate_overlap_count,
    extract_panel_triangles,
    prisms_intersect,
    transformed_prisms,
)


class _IdentityTransform:
    def point(self, p):
        return p


def prism(x=0.0, y=0.0, z=0.0):
    top = ((x, y, z), (x + 2, y, z), (x, y + 2, z))
    bottom = tuple((p[0], p[1], p[2] - 1) for p in top)
    return top + bottom


class ReverseMeshScreenTests(unittest.TestCase):
    def test_overlapping_prisms(self):
        self.assertTrue(prisms_intersect(prism(), prism(0.5, 0.5)))
        self.assertFalse(prisms_intersect(prism(), prism(5, 5)))
        self.assertFalse(prisms_intersect(prism(), prism(0, 0, -1)))

    def test_cached_triangles_and_no_source_geometry_mutation(self):
        calls = []

        def tessellate(deviation):
            calls.append(deviation)
            return (((0, 0, 0), (2, 0, 0), (0, 2, 0)), ((0, 1, 2),))

        panel = SimpleNamespace(panel_id="P01")
        face = SimpleNamespace(tessellate=tessellate)
        build = SimpleNamespace(panels=(panel,), panel_faces=(face,))
        meshes = extract_panel_triangles(build)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(meshes["P01"]), 1)
        transformed = transformed_prisms(meshes["P01"], _IdentityTransform(), 1.0)
        self.assertEqual(len(transformed), 1)
        self.assertEqual(approximate_overlap_count(transformed, transformed), 1)
        self.assertEqual(meshes["P01"][0][0], (0.0, 0.0, 0.0))

    def test_missing_triangulation_is_unknown_not_empty(self):
        panel = SimpleNamespace(panel_id="P01")
        build = SimpleNamespace(panels=(panel,), panel_faces=(object(),))
        self.assertEqual(extract_panel_triangles(build), {})


if __name__ == "__main__":
    unittest.main()
