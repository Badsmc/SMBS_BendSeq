import unittest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec, gp_Pnt

from smbs_core.workers.baseline.worker import BaselineWorker


class TestBaselineWorkerHausdorff(unittest.TestCase):
    """
    Unit tests for BaselineWorker real Hausdorff distance calculation
    and surface discrepancy detection.
    """

    def _make_box(self, dx: float = 20.0, dy: float = 20.0, dz: float = 20.0):
        return BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(dx, dy, dz)).Shape()

    def _make_l_shape(self, dx: float = 20.0, dy: float = 20.0, dz: float = 20.0):
        # L-shaped solid spanning the full 20x20x20 bounding box:
        # Base plate 20x20x10 + vertical flange 20x10x20 fused together
        f1 = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(dx, dy, dz / 2.0)).Shape()
        f2 = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(dx, dy / 2.0, dz)).Shape()
        fuse = BRepAlgoAPI_Fuse(f1, f2)
        fuse.Build()
        return fuse.Shape()

    def _make_translated(self, shape, dx: float = 1.0, dy: float = 0.0, dz: float = 0.0):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(dx, dy, dz))
        return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    def test_01_identity_hausdorff_is_zero(self):
        """Identical shapes produce exactly 0.0 Hausdorff distance."""
        box = self._make_box(20.0, 20.0, 20.0)
        dist = BaselineWorker._compute_hausdorff_distance(box, box)
        self.assertAlmostEqual(dist, 0.0, places=5)

    def test_02_different_surface_same_bbox(self):
        """
        Acceptance #3 algorithm test:
        Full cube 20x20x20 vs L-shaped solid have IDENTICAL bounding box ([0,20]x[0,20]x[0,20]),
        but real Hausdorff distance is 10.0 mm (exceeding surface_distance_tol_mm = 0.2 mm)
        due to the missing quadrant vertices in the full cube.
        """
        cube = self._make_box(20.0, 20.0, 20.0)
        l_shape = self._make_l_shape(20.0, 20.0, 20.0)

        # Check properties directly
        _, _, bbox_cube = BaselineWorker()._get_shape_properties(cube)
        _, _, bbox_l = BaselineWorker()._get_shape_properties(l_shape)

        bbox_delta = [abs(a - b) for a, b in zip(bbox_cube, bbox_l)]
        self.assertTrue(all(d < 1e-4 for d in bbox_delta), f"BBox should be identical, got delta: {bbox_delta}")

        # Real Hausdorff distance must detect the 10.0mm discrepancy
        dist = BaselineWorker._compute_hausdorff_distance(cube, l_shape)
        self.assertGreater(dist, 0.2, f"Expected hausdorff > 0.2mm, got {dist:.4f}")
        self.assertAlmostEqual(dist, 10.0, places=2)

    def test_03_translated_box_sanity(self):
        """Sanity test verifying vertex distance under 1mm translation."""
        box1 = self._make_box(20.0, 20.0, 20.0)
        box2 = self._make_translated(box1, dx=1.0)
        dist = BaselineWorker._compute_hausdorff_distance(box1, box2)
        self.assertGreater(dist, 0.0)
        self.assertAlmostEqual(dist, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
