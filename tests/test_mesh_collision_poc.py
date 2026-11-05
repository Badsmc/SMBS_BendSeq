"""Headless unit tests for FreeCAD Mesh PoC fail-closed classifier.

Mocks deliberately do not claim FreeCAD's mesh Boolean correctness; actual
FreeCAD runtime regression is in tools/run_d6_mesh_poc.py.
"""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.mesh_collision_poc import (
    MeshContact, bbox_gap_mm, classify_mesh_pair, benchmark_mesh_pair,
)


def box(x0, y0, z0, x1, y1, z1):
    return SimpleNamespace(XMin=x0, YMin=y0, ZMin=z0,
                           XMax=x1, YMax=y1, ZMax=z1)


class FakeMesh:
    def __init__(self, bbox, facets=12, solid=True, self_cross=False,
                 result=None, volume=0):
        self.BoundBox = bbox
        self.CountFacets = facets
        self._solid = solid
        self._self_cross = self_cross
        self._result = result
        self.Volume = volume
    def isSolid(self):
        return self._solid
    def hasSelfIntersections(self):
        return self._self_cross
    def intersect(self, other):
        return self._result


class MeshPoCTests(unittest.TestCase):
    def setUp(self):
        self.first = FakeMesh(box(0,0,0,10,10,10))

    def test_bbox_separated_3d_is_clear(self):
        second = FakeMesh(box(20,0,0,30,10,10))
        self.assertEqual(classify_mesh_pair(self.first, second).status, MeshContact.CLEAR)

    def test_touch_is_unverified_even_empty_boolean(self):
        second = FakeMesh(box(10,0,0,20,10,10), result=FakeMesh(
            box(0,0,0,0,0,0), facets=0))
        got = classify_mesh_pair(self.first, second)
        self.assertEqual(got.status, MeshContact.UNVERIFIED)

    def test_positive_intersection_volume(self):
        second = FakeMesh(box(8,0,0,18,10,10),
                          result=FakeMesh(box(8,0,0,10,10,10), facets=12, volume=200.0))
        got = classify_mesh_pair(self.first, second)
        self.assertEqual(got.status, MeshContact.PENETRATION)
        self.assertEqual(got.overlap_volume_mm3, 200.0)

    def test_no_volume_with_overlapping_boxes_unverified(self):
        second = FakeMesh(box(8,0,0,18,10,10),
                          result=FakeMesh(box(8,0,0,10,10,10), facets=12, volume=0.0))
        self.assertEqual(classify_mesh_pair(self.first, second).status,
                         MeshContact.UNVERIFIED)

    def test_not_closed_is_unverified(self):
        second = FakeMesh(box(20,0,0,30,10,10), solid=False)
        self.assertEqual(classify_mesh_pair(self.first, second).status,
                         MeshContact.UNVERIFIED)

    def test_self_intersections_unverified(self):
        second = FakeMesh(box(20,0,0,30,10,10), self_cross=True)
        self.assertEqual(classify_mesh_pair(self.first, second).status,
                         MeshContact.UNVERIFIED)

    def test_boolean_exception_unverified(self):
        second = FakeMesh(box(8,0,0,18,10,10))
        def broken(*_):
            raise RuntimeError("failed")
        got = classify_mesh_pair(self.first, second, narrow_phase=broken)
        self.assertEqual(got.status, MeshContact.UNVERIFIED)
        self.assertIn("failed", got.reason)

    def test_invalid_bbox_unverified(self):
        second = FakeMesh(box(float("nan"),0,0,10,10,10))
        self.assertEqual(classify_mesh_pair(self.first, second).status,
                         MeshContact.UNVERIFIED)

    def test_bbox_distance(self):
        self.assertAlmostEqual(bbox_gap_mm(box(0,0,0,1,1,1),
                                           box(4,5,1,6,6,3)), 5)
        self.assertEqual(bbox_gap_mm(box(0,0,0,1,1,1),
                                     box(1,0,0,2,1,1)), 0)

    def test_static_benchmark_counts(self):
        second = FakeMesh(box(20,0,0,30,10,10))
        result = benchmark_mesh_pair(self.first, second, iterations=5)
        self.assertEqual(result["statuses"]["CLEAR"], 5)
        self.assertEqual(result["iterations"], 5)
        self.assertIn("Not CCD", result["note"])

    def test_invalid_benchmark_budget(self):
        with self.assertRaises(ValueError):
            benchmark_mesh_pair(self.first, self.first, iterations=0)


if __name__ == "__main__":
    unittest.main()
