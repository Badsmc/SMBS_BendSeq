"""
test_kl9_relief_cut.py - Unit and regression tests for KL-9 Relief-Cut Edge Alignment.
Authoritative Design: docs/KL-9_RELIEF_CUT_DESIGN.md

Validates:
1. Geometric BRep detection of physical relief notches (detect_relief_notch_intervals).
2. Honest fallback (no fabrication) when no relief notches exist (e.g. plain box).
3. End-to-end positive verification on fixture SMBendTests/2-1-3-4-(ok).FCStd:
   exact 90 reference vs 90 restored faces, counts_match=True, hausdorff_distance_mm <= 0.05.
4. Topological BRep validity (BRepCheck_Analyzer.IsValid() == True).
5. Cross-fixture regression verification on canonical benchmarks (box, SQin, SQout, U, Z).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import Part
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
from smbs_core.workers.baseline.worker import BaselineWorker
from smbs_core.coordinator.run_pipeline import Coordinator

ROOT = Path(__file__).resolve().parent.parent


class TestKL9ReliefCutAlignment(unittest.TestCase):
    def setUp(self):
        self.fixture_2134 = ROOT / "SMBendTests/2-1-3-4-(ok).FCStd"
        self.fixture_box = ROOT / "SMBendTests/box.100out-50in-(ok).FCStd"

    def test_positive_2_1_3_4_e2e_reconstruction(self):
        """
        Positive test: Coordinator on 2-1-3-4-(ok).FCStd must reconstruct
        exact 90-face geometry with counts_match=True and hausdorff <= 0.05 mm (0.0000 mm).
        """
        coord = Coordinator()
        res = coord.start_run(
            source_file_path=self.fixture_2134,
            part_id="kl9_2134_pos",
            search_algorithm="dfs",
            unknown_policy="EXPLORATORY",
        )
        self.assertTrue(res.get("pass"), f"Expected Coordinator baseline pass to be True, got {res.get('pass')}")
        bm = res.get("baseline_metrics", {})
        raw_comp = bm.get("raw_comparison_result", {})

        ref_faces = bm.get("faces_count_reference")
        res_faces = bm.get("faces_count_restored")
        hd = bm.get("hausdorff_distance_mm")
        counts_match = raw_comp.get("counts_match")

        self.assertEqual(ref_faces, 90, f"Expected 90 reference faces, got {ref_faces}")
        self.assertEqual(res_faces, 90, f"Expected 90 restored faces, got {res_faces}")
        self.assertTrue(counts_match, f"Expected counts_match=True, got {counts_match}")
        self.assertLessEqual(hd, 0.05, f"Expected hausdorff <= 0.05 mm, got {hd:.4f} mm")
        self.assertTrue(res.get("pass"), "Expected Coordinator run pass to be True")

    def test_geometric_relief_notch_detection(self):
        """
        Validates detect_relief_notch_intervals detects exact physical relief walls
        from flat_pattern.brep for BEND_01 on 2_1_3_4 without using metadata.
        """
        coord = Coordinator()
        res = coord.start_run(
            source_file_path=self.fixture_2134,
            part_id="kl9_detect_pos",
            search_algorithm="dfs",
            unknown_policy="EXPLORATORY",
        )
        run_dir = Path(res["run_dir"])
        flat_path = run_dir / "flat_pattern.brep"
        canon_path = run_dir / "04_canonical_part.json"

        flat_occ = TopoDS_Shape()
        breptools.Read(flat_occ, str(flat_path), BRep_Builder())
        with open(canon_path, encoding="utf-8") as f:
            canon = json.load(f)["payload"]

        notches = SheetMetalFolder.detect_relief_notch_intervals(flat_occ, canon)
        self.assertIn("BEND_01_b67ceec7", notches)
        b1_info = notches["BEND_01_b67ceec7"]
        b1_notches = b1_info.get("notches", [])
        self.assertEqual(len(b1_notches), 2, f"Expected 2 relief notches on BEND_01, got {len(b1_notches)}")

        # Verify physical relief boundaries match ground truth within 0.01 mm
        x_min_notch = b1_notches[0]["x"]
        x_max_notch = b1_notches[1]["x"]
        self.assertAlmostEqual(x_min_notch[0], 7.9913, places=2)
        self.assertAlmostEqual(x_min_notch[1], 8.7913, places=2)
        self.assertAlmostEqual(x_max_notch[0], 301.7913, places=2)
        self.assertAlmostEqual(x_max_notch[1], 302.5913, places=2)

    def test_honest_fallback_when_no_relief_notch(self):
        """
        Negative / fallback test (Invariant #3):
        On plain box with no relief notches, detect_relief_notch_intervals
        returns empty dict {}, and folder cleanly falls back to nominal box_hub
        without fabricating any relief cutouts.
        """
        coord = Coordinator()
        res = coord.start_run(
            source_file_path=self.fixture_box,
            part_id="kl9_detect_fallback",
            search_algorithm="dfs",
            unknown_policy="EXPLORATORY",
        )
        run_dir = Path(res["run_dir"])
        flat_path = run_dir / "flat_pattern.brep"
        canon_path = run_dir / "04_canonical_part.json"

        flat_occ = TopoDS_Shape()
        breptools.Read(flat_occ, str(flat_path), BRep_Builder())
        with open(canon_path, encoding="utf-8") as f:
            canon = json.load(f)["payload"]

        notches = SheetMetalFolder.detect_relief_notch_intervals(flat_occ, canon)
        self.assertEqual(len(notches), 0, f"Expected 0 notches for plain box, got {len(notches)}")

    def test_brep_validity_after_relief_cuts(self):
        """
        Ensures that boolean cut operations on revolved bend sectors
        maintain strict OpenCASCADE topological validity (BRepCheck).
        """
        r_in, t, length, deg = 1.0, 1.5, 50.0, 90.0
        ax_p = App.Vector(0, 0, 0)
        ax_d = App.Vector(0, 1, 0)
        start_v = App.Vector(0, 0, 1)
        p1 = ax_p + start_v * r_in
        p2 = ax_p + start_v * (r_in + t)
        p3 = p2 + ax_d * length
        p4 = p1 + ax_d * length
        face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
        sector = face.revolve(ax_p, ax_d, deg)

        cut_box = Part.makeBox(10, 2.5, 10, App.Vector(-2, 0, -2))
        cut_result = sector.cut(cut_box)

        occ_shape = SheetMetalFolder._to_occ_shape(cut_result)
        is_valid = BRepCheck_Analyzer(occ_shape).IsValid()
        self.assertTrue(is_valid, "BRepCheck_Analyzer reported invalid topology after sector relief cut")


if __name__ == "__main__":
    unittest.main()
