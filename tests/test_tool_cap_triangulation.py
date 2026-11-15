"""Tool cap triangulation must preserve a concave physical cross-section.

These tests use pure Python; Pivy, Coin3D and FreeCAD are not required.
"""
from __future__ import annotations

import unittest

from freecad_smbs.ui.tool_cap_triangulation import (
    CapTriangulationError, triangulate_tool_cap,
)


def area(points):
    return abs(sum(
        a[0] * b[1] - b[0] * a[1]
        for a, b in zip(points, points[1:] + points[:1])
    )) / 2.0


class TestToolCapTriangulation(unittest.TestCase):
    def test_concave_l_profile_does_not_fill_missing_corner(self):
        # The missing upper-right 2x2 region must stay empty.
        outline = [(0,0), (3,0), (3,1), (1,1), (1,3), (0,3)]
        vertices = [(x, -50., z) for x, z in outline]
        triangles = triangulate_tool_cap(vertices)
        self.assertEqual(len(triangles), 4)
        triangles_area = sum(
            area([outline[i] for i in triangle]) for triangle in triangles
        )
        self.assertAlmostEqual(triangles_area, 5.)
        self.assertEqual(set(i for t in triangles for i in t),
                         set(range(len(vertices))))

    def test_reverse_winding_matches_profile(self):
        vertices = [(x, 50., z) for x, z in
                    [(0,0), (0,3), (1,3), (1,1), (3,1), (3,0)]]
        triangles = triangulate_tool_cap(vertices)
        self.assertEqual(len(triangles), 4)
        self.assertAlmostEqual(sum(
            area([(vertices[i][0], vertices[i][2]) for i in t])
            for t in triangles
        ), 5.)

    def test_closed_polygon_and_collinear_edge(self):
        points = [(0,0), (1,0), (2,0), (2,2), (0,2), (0,0)]
        vertices = [(x, 25., z) for x, z in points]
        triangles = triangulate_tool_cap(vertices)
        self.assertAlmostEqual(sum(
            area([points[i] for i in t]) for t in triangles
        ), 4.)

    def test_rejects_nonplanar_and_degenerate(self):
        with self.assertRaises(CapTriangulationError):
            triangulate_tool_cap([(0,0,0), (1,1,0), (0,0,1)])
        with self.assertRaises(CapTriangulationError):
            triangulate_tool_cap([(0,0,0), (1,0,0), (2,0,0)])

    def test_renderer_uses_triangulation_only_for_tool_caps(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        renderer = (root / "freecad_smbs/ui/coin3d_viewer.py").read_text()
        self.assertIn('if face.kind in ("punch_cap", "die_cap"):', renderer)
        self.assertIn("triangulate_tool_cap(face.vertices)", renderer)
        self.assertIn("if face.holes:\n        return None", renderer)
        self.assertIn("facets = coin.SoIndexedFaceSet()", renderer)


if __name__ == "__main__":
    unittest.main()
