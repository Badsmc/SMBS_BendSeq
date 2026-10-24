import unittest
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec, gp_Pnt, gp_Ax1, gp_Dir
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_VERTEX
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from pathlib import Path

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

    def test_04_hausdorff_dedup_equivalence(self):
        """
        Pin test for P1-perf optimization (Proposal 1):
        Verifies that deduplicated vertex evaluation produces bit-identical
        results to legacy TopExp_Explorer iteration on non-trivial geometries.
        """
        def legacy_hausdorff(s1, s2):
            def directed(sa, sb):
                max_d = 0.0
                exp = TopExp_Explorer(sa, TopAbs_VERTEX)
                while exp.More():
                    v = exp.Current()
                    ext = BRepExtrema_DistShapeShape(v, sb)
                    ext.Perform()
                    if ext.IsDone():
                        d = ext.Value()
                        if d > max_d:
                            max_d = d
                    exp.Next()
                return max_d
            return max(directed(s1, s2), directed(s2, s1))

        # 1. Rotated box 15 deg
        box = self._make_box(20.0, 20.0, 20.0)
        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(gp_Pnt(10, 10, 10), gp_Dir(1, 1, 0)), 0.261799)
        rot_box = BRepBuilderAPI_Transform(box, trsf, True).Shape()

        h_legacy = legacy_hausdorff(box, rot_box)
        h_dedup = BaselineWorker._compute_hausdorff_distance(box, rot_box)
        self.assertAlmostEqual(h_legacy, h_dedup, places=9)

        # 2. Translated four_bend synthetic sheetmetal fixture
        four_bend_path = Path("fixtures/synthetic/four_bend.brep").resolve()
        if four_bend_path.exists():
            fb = BaselineWorker(None)._load_brep(four_bend_path)
            trsf_fb = gp_Trsf()
            trsf_fb.SetTranslation(gp_Vec(0.5, 0.2, -0.3))
            fb_off = BRepBuilderAPI_Transform(fb, trsf_fb, True).Shape()

            h_fb_legacy = legacy_hausdorff(fb, fb_off)
            h_fb_dedup = BaselineWorker._compute_hausdorff_distance(fb, fb_off)
            self.assertAlmostEqual(h_fb_legacy, h_fb_dedup, places=9)


if __name__ == "__main__":
    unittest.main()
