"""
test_handmod_fixtures.py - Smoke and validation tests for HandMod Phase 1 fixtures.

Verifies:
1. Procedural generation is idempotent.
2. test_box.FCStd has valid solid Body and Unfold objects.
3. test_pentagon.FCStd has valid solid Body with 5 bend features.
4. test_mirror.FCStd has valid Body, Unfold, and TechDraw Page/View structure.
"""
from __future__ import annotations

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
from tests.fixtures.create_test_part import generate_all_fixtures, FIXTURES_DIR


class TestHandModFixtures(unittest.TestCase):
    """Test suite for Phase 1 HandMod regression fixtures."""

    @classmethod
    def setUpClass(cls):
        # Run generator to ensure fixtures exist
        cls.fixtures = generate_all_fixtures()

    def test_idempotent_regeneration(self):
        """Verify running generation again produces valid files without error."""
        paths = generate_all_fixtures()
        for name, p in paths.items():
            self.assertTrue(p.exists(), f"Fixture {name} must exist at {p}")
            self.assertGreater(p.stat().st_size, 1000, f"Fixture {name} size must be > 1KB")

    def test_box_structure(self):
        """Verify test_box.FCStd contains Body and Unfold shapes with valid solid geometry."""
        box_path = FIXTURES_DIR / "test_box.FCStd"
        doc = App.openDocument(str(box_path))
        try:
            body = doc.getObject("Body")
            unfold = doc.getObject("Unfold")
            self.assertIsNotNone(body, "test_box must have Body object")
            self.assertIsNotNone(unfold, "test_box must have Unfold object")

            self.assertFalse(body.Shape.isNull(), "Body shape must not be null")
            self.assertFalse(unfold.Shape.isNull(), "Unfold shape must not be null")
            self.assertGreater(len(body.Shape.Solids), 0, "Body must have at least 1 Solid")
            self.assertGreater(len(body.Shape.Faces), 5, "Body must have multiple faces")
        finally:
            App.closeDocument(doc.Name)

    def test_pentagon_structure(self):
        """Verify test_pentagon.FCStd contains multi-bend body and unfold."""
        pentagon_path = FIXTURES_DIR / "test_pentagon.FCStd"
        doc = App.openDocument(str(pentagon_path))
        try:
            body = doc.getObject("Body")
            unfold = doc.getObject("Unfold")
            self.assertIsNotNone(body, "test_pentagon must have Body object")
            self.assertIsNotNone(unfold, "test_pentagon must have Unfold object")

            self.assertFalse(body.Shape.isNull())
            self.assertGreater(len(body.Shape.Faces), 10, "5-bend body must have >10 faces")
        finally:
            App.closeDocument(doc.Name)

    def test_pentagon_has_5_cylindrical_bends(self):
        """C2: Verify test_pentagon has exactly 5 distinct cylindrical bend features."""
        from freecad_smbs.manual.geometry_extract import extract_bend_lines_3d
        pentagon_path = FIXTURES_DIR / "test_pentagon.FCStd"
        doc = App.openDocument(str(pentagon_path))
        try:
            body = doc.getObject("Body")
            raw_bends = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends), 5, f"test_pentagon Body must contain 5 cylindrical bend features, got {len(raw_bends)}")
        finally:
            App.closeDocument(doc.Name)

    def test_mirror_structure(self):
        """Verify test_mirror.FCStd contains Body, Unfold, and TechDraw Page."""
        mirror_path = FIXTURES_DIR / "test_mirror.FCStd"
        doc = App.openDocument(str(mirror_path))
        try:
            body = doc.getObject("Body")
            unfold = doc.getObject("Unfold")
            self.assertIsNotNone(body, "test_mirror must have Body object")
            self.assertIsNotNone(unfold, "test_mirror must have Unfold object")

            # Check TechDraw page presence
            page = doc.getObject("Page")
            self.assertIsNotNone(page, "test_mirror must have TechDraw Page object")
        finally:
            App.closeDocument(doc.Name)

    def test_mirror_has_3_cylindrical_bends(self):
        """C2: Verify test_mirror has exactly 3 distinct cylindrical bend features."""
        from freecad_smbs.manual.geometry_extract import extract_bend_lines_3d
        mirror_path = FIXTURES_DIR / "test_mirror.FCStd"
        doc = App.openDocument(str(mirror_path))
        try:
            body = doc.getObject("Body")
            raw_bends = extract_bend_lines_3d(body.Shape)
            self.assertEqual(len(raw_bends), 3, f"test_mirror Body must contain 3 cylindrical bend features, got {len(raw_bends)}")
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
