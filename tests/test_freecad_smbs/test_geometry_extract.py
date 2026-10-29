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

    def test_match_to_canonical_ambiguous(self):
        """Tie-break logic properly discriminates between two close bend candidates."""
        # Synthetic ambiguous canonical part with two nearly identical lines
        ambiguous_04 = {
            "payload": {
                "bends": {
                    "BEND_NEAR_A": {
                        "axis_point": [50.0, 0.0, 1.5],
                        "axis_direction": [1.0, 0.0, 0.0],
                        "radius_mm": {"value": 1.0},
                        "length_mm": {"value": 100.0},
                        "parent_panel_id": "PANEL_A"
                    },
                    "BEND_NEAR_B": {
                        "axis_point": [50.0, 0.5, 1.5],
                        "axis_direction": [1.0, 0.0, 0.0],
                        "radius_mm": {"value": 1.0},
                        "length_mm": {"value": 100.0},
                        "parent_panel_id": "PANEL_B"
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
        matched = match_to_canonical(raw_test, ambiguous_04)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].bend_id, "BEND_NEAR_A", "Must pick exact geometric match A over offset B")

    def test_plane_normalization(self):
        """Unfold contour extraction is robust to shape placement changes."""
        doc = App.openDocument(str(self.box_fcstd))
        try:
            unfold_shape = extract_unfold_solid(doc, "Unfold")
            pts1 = extract_outer_contour_unfold_3d(unfold_shape)
            
            # Translate shape in Z and rotate
            translated_shape = unfold_shape.copy()
            translated_shape.translate(App.Vector(0, 0, 50.0))
            pts2 = extract_outer_contour_unfold_3d(translated_shape)

            self.assertEqual(len(pts1), len(pts2))
            for p1, p2 in zip(pts1, pts2):
                self.assertAlmostEqual(p1[0], p2[0], places=3)
                self.assertAlmostEqual(p1[1], p2[1], places=3)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
