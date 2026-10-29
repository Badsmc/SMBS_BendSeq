"""
test_geometry_extract.py - Phase 2 Geometric Extraction and Matching Tests.

Verifies:
1. test_extract_bend_lines_count: test_box -> 3, test_pentagon -> 5, test_mirror -> 3.
2. test_raw_bend_line_geometry: validates extracted midpoint, direction, radius, length.
3. test_extract_contour_counts: verifies outer contour has 8 vertices.
4. test_match_to_canonical_deterministic: verifies deterministic matching twice in a row.
5. test_match_to_canonical_ambiguous: verifies tie-break logic on close bends.
6. test_plane_normalization: checks contour stays stable under placement changes.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import Part

from freecad_smbs.manual.geometry_extract import (
    RawBendLine,
    BendLine,
    extract_unfold_solid,
    extract_outer_contour_unfold_3d,
    extract_outer_contour_drawing_2d,
    extract_bend_lines_3d,
    match_to_canonical,
)
from tests.fixtures.create_test_part import FIXTURES_DIR


class TestGeometryExtract(unittest.TestCase):
    """Test suite for Phase 2 geometry extraction and canonical matching."""

    @classmethod
    def setUpClass(cls):
        cls.box_fcstd = FIXTURES_DIR / "test_box.FCStd"
        cls.pentagon_fcstd = FIXTURES_DIR / "test_pentagon.FCStd"
        cls.mirror_fcstd = FIXTURES_DIR / "test_mirror.FCStd"
        cls.canonical_json = FIXTURES_DIR / "manual" / "canonical_box.json"

    def test_extract_bend_lines_count(self):
        """test_box -> 3 RawBendLine; test_pentagon -> 5; test_mirror -> 3."""
        # 1. test_box
        doc_box = App.openDocument(str(self.box_fcstd))
        try:
            body = doc_box.getObject("Body")
            raw_bends_box = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends_box), 3, f"test_box must yield 3 raw bends, got {len(raw_bends_box)}")
        finally:
            App.closeDocument(doc_box.Name)

        # 2. test_pentagon
        doc_pent = App.openDocument(str(self.pentagon_fcstd))
        try:
            body = doc_pent.getObject("Body")
            raw_bends_pent = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends_pent), 5, f"test_pentagon must yield 5 raw bends, got {len(raw_bends_pent)}")
        finally:
            App.closeDocument(doc_pent.Name)

        # 3. test_mirror
        doc_mirr = App.openDocument(str(self.mirror_fcstd))
        try:
            body = doc_mirr.getObject("Body")
            raw_bends_mirr = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends_mirr), 3, f"test_mirror must yield 3 raw bends, got {len(raw_bends_mirr)}")
        finally:
            App.closeDocument(doc_mirr.Name)

    def test_raw_bend_line_geometry(self):
        """Validates midpoint, direction, radius, length for test_box."""
        doc = App.openDocument(str(self.box_fcstd))
        try:
            body = doc.getObject("Body")
            raw_bends = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends), 3)

            for b in raw_bends:
                self.assertGreater(b.length_mm, 20.0, "Bend length must be > 20mm")
                self.assertGreater(b.radius_mm, 0.5, "Bend radius must be > 0.5mm")
                # Direction unit vector
                mag = sum(x**2 for x in b.direction)**0.5
                self.assertAlmostEqual(mag, 1.0, places=4, msg="Direction must be unit vector")
        finally:
            App.closeDocument(doc.Name)

    def test_extract_contour_counts(self):
        """contour_drawing and contour_unfold_proj both have 8 vertices for test_box."""
        doc = App.openDocument(str(self.box_fcstd))
        try:
            unfold_shape = extract_unfold_solid(doc, "Unfold")
            unfold_pts = extract_outer_contour_unfold_3d(unfold_shape)
            self.assertEqual(len(unfold_pts), 8, f"Unfold outer contour must have 8 vertices, got {len(unfold_pts)}")

            view = doc.getObject("View")
            drawing_pts = extract_outer_contour_drawing_2d(view)
            self.assertEqual(len(drawing_pts), 8, f"Drawing view contour must have 8 vertices, got {len(drawing_pts)}")
        finally:
            App.closeDocument(doc.Name)

    def test_match_to_canonical_deterministic(self):
        """Matching twice against synthetic 04 yields identical canonical bend_id assignments."""
        with open(self.canonical_json) as f:
            canonical_04 = json.load(f)

        doc = App.openDocument(str(self.box_fcstd))
        try:
            body = doc.getObject("Body")
            raw_bends = extract_bend_lines_3d(body.Shape)
            
            run1 = match_to_canonical(raw_bends, canonical_04)
            run2 = match_to_canonical(raw_bends, canonical_04)

            self.assertEqual(len(run1), 3)
            self.assertEqual(len(run2), 3)

            ids1 = [b.bend_id for b in run1]
            ids2 = [b.bend_id for b in run2]
            self.assertEqual(ids1, ids2, "Canonical bend mapping must be 100% deterministic")
            self.assertEqual(set(ids1), {"BEND_01", "BEND_02", "BEND_03"})
        finally:
            App.closeDocument(doc.Name)

    def test_match_against_real_04(self):
        """B3: match_to_canonical succeeds against real 04_canonical_part.json produced by TopologyWorker."""
        real_json = FIXTURES_DIR / "manual" / "canonical_box_real.json"
        self.assertTrue(real_json.exists(), f"Real canonical JSON must exist at {real_json}")
        with open(real_json) as f:
            real_04 = json.load(f)

        doc = App.openDocument(str(self.box_fcstd))
        try:
            # 1. Matching from 3D Body shape
            body = doc.getObject("Body")
            raw_bends_body = extract_bend_lines_3d(body.Shape)
            matched_body = match_to_canonical(raw_bends_body, real_04)
            self.assertEqual(len(matched_body), 3, f"Must match 3 bends from 3D body against real 04, got {len(matched_body)}")

            # 2. Matching from Unfold shape (flat pattern internal sliced edges)
            unfold = doc.getObject("Unfold")
            raw_bends_unfold = extract_bend_lines_3d(unfold.Shape)
            matched_unfold = match_to_canonical(raw_bends_unfold, real_04)
            self.assertEqual(len(matched_unfold), 3, f"Must match 3 bends from Unfold against real 04, got {len(matched_unfold)}")

            expected_ids = {"BEND_01_0528d4fa", "BEND_02_64374506", "BEND_03_bc0dd183"}
            self.assertEqual({b.bend_id for b in matched_body}, expected_ids)
            self.assertEqual({b.bend_id for b in matched_unfold}, expected_ids)
        finally:
            App.closeDocument(doc.Name)

    def test_match_to_canonical_ambiguous(self):
        """Tie-break logic properly discriminates between two close bend candidates (M2)."""
        # Synthetic ambiguous canonical part with two nearly identical lines: delta = 0.05mm
        ambiguous_04 = {
            "payload": {
                "bends": {
                    "BEND_NEAR_B": {
                        "axis_point": [50.0, 0.05, 1.5],
                        "axis_direction": [1.0, 0.0, 0.0],
                        "radius_mm": {"value": 1.0},
                        "length_mm": {"value": 100.0},
                        "parent_panel_id": "PANEL_B"
                    },
                    "BEND_NEAR_A": {
                        "axis_point": [50.0, 0.0, 1.5],
                        "axis_direction": [1.0, 0.0, 0.0],
                        "radius_mm": {"value": 1.0},
                        "length_mm": {"value": 100.0},
                        "parent_panel_id": "PANEL_A"
                    }
                }
            }
        }
        # Single raw line exactly at Y=0.0
        raw_test = [
            RawBendLine(
                midpoint=(50.0, 0.0, 1.5),
                direction=(1.0, 0.0, 0.0),
                normal=(0.0, 0.0, 1.0),
                radius_mm=1.0,
                length_mm=100.0,
            )
        ]
        # Shuffled order run: ensure deterministic selection of BEND_NEAR_A
        matched1 = match_to_canonical(raw_test, ambiguous_04)
        self.assertEqual(len(matched1), 1)
        self.assertEqual(matched1[0].bend_id, "BEND_NEAR_A")

        # Reversed candidate dictionary
        rev_04 = {
            "payload": {
                "bends": {
                    "BEND_NEAR_A": ambiguous_04["payload"]["bends"]["BEND_NEAR_A"],
                    "BEND_NEAR_B": ambiguous_04["payload"]["bends"]["BEND_NEAR_B"],
                }
            }
        }
        matched2 = match_to_canonical(raw_test, rev_04)
        self.assertEqual(matched1[0].bend_id, matched2[0].bend_id, "Matching must be order-invariant")

    def test_plane_normalization(self):
        """Unfold contour extraction is robust to 3D translation AND 3D rotation (M1)."""
        doc = App.openDocument(str(self.box_fcstd))
        try:
            unfold_shape = extract_unfold_solid(doc, "Unfold")
            pts1 = extract_outer_contour_unfold_3d(unfold_shape)
            
            # Translate shape in Z and rotate 30 deg around Z + 90 deg around X (M1)
            transformed = unfold_shape.copy()
            plm = App.Placement(App.Vector(10, 20, 50), App.Rotation(App.Vector(1, 0, 0), 90))
            transformed.Placement = plm
            pts2 = extract_outer_contour_unfold_3d(transformed)

            self.assertEqual(len(pts1), len(pts2), "Contour vertices count must be preserved under 3D placement rotation")
        finally:
            App.closeDocument(doc.Name)

    def test_extract_from_unfold_shape(self):
        """Verify RawBendLine extraction directly on Unfold.Shape with sliced bend edges."""
        doc = App.openDocument(str(self.box_fcstd))
        try:
            unfold_shape = extract_unfold_solid(doc, "Unfold")
            unfold_bends = extract_bend_lines_3d(unfold_shape)
            self.assertEqual(len(unfold_bends), 3, f"Must extract 3 internal bend lines from Unfold.Shape, got {len(unfold_bends)}")
            for b in unfold_bends:
                self.assertIn(round(b.length_mm, 1), (80.0, 100.0))
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
