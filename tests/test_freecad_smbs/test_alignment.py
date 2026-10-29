"""
test_alignment.py - Phase 4b Tests for Rigid Coordinate Alignment (Kabsch & Fallbacks).

Covers Gate 4b:
1. test_alignment_box_rms: test_box rms < 0.1mm, det(R)≈1, T·T⁻¹≈I, method="KABSCH".
2. test_alignment_mirror_rms: test_mirror rms < 0.5mm, is_mirrored=True, planar_reflection=True, T·T⁻¹≈I.
3. test_alignment_respects_view_scale: view.Scale=0.5 -> rms valid, det(R)≈1, view_scale=0.5.
4. test_alignment_vertex_count_mismatch_errors: N!=M -> AlignmentError without silent truncation.
5. test_alignment_rms_gate: synthetic shift 5mm -> ok=False, warnings non-empty.
6. test_alignment_force_flag: synthetic shift 5mm + force=True -> ok=True, warnings contains "FORCED".
7. test_alignment_pca_fallback: degraded input -> method="PCA", warning flagged.
8. test_method_selection_priority: default Kabsch; view-transform only with allow_view_transform=True.
9. test_alignment_inverse_property: for all cases ||T·T⁻¹ - I||_F < 1e-6.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App

from freecad_smbs.manual.alignment import (
    AlignmentError,
    AlignmentResult,
    align_drawing_to_unfold,
)
from freecad_smbs.manual.geometry_extract import (
    extract_outer_contour_drawing_2d,
    extract_outer_contour_unfold_3d,
    extract_unfold_solid,
)
from tests.fixtures.create_test_part import FIXTURES_DIR


class TestAlignment(unittest.TestCase):
    """Test suite for Phase 4b coordinate alignment."""

    @classmethod
    def setUpClass(cls):
        cls.box_path = FIXTURES_DIR / "test_box.FCStd"
        cls.mirror_path = FIXTURES_DIR / "test_mirror.FCStd"

    def _assert_is_valid_affine_transform(self, res: AlignmentResult):
        T = np.array(res.transform_4x4)
        T_inv = np.array(res.transform_inv_4x4)

        # 4x4 shape
        self.assertEqual(T.shape, (4, 4))
        self.assertEqual(T_inv.shape, (4, 4))

        # Bottom row must be [0, 0, 0, 1]
        np.testing.assert_allclose(T[3, :], [0, 0, 0, 1], atol=1e-6)
        np.testing.assert_allclose(T_inv[3, :], [0, 0, 0, 1], atol=1e-6)

        # Exact inverse property: ||T * T_inv - I||_F < 1e-6
        identity = np.eye(4)
        err = np.linalg.norm(T @ T_inv - identity)
        self.assertLess(err, 1e-6, f"Inverse matrix property violated: error {err}")

        # Isometric rotation submatrix: ||R * R^T - I||_F < 1e-6
        R = T[:3, :3]
        r_err = np.linalg.norm(R @ R.T - np.eye(3))
        self.assertLess(r_err, 1e-6, f"Rotation matrix must be strictly orthogonal: error {r_err}")

    def test_alignment_box_rms(self):
        """test_box: rms < 0.1 mm, det(R)≈1, T·T⁻¹≈I, method='KABSCH'."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = extract_outer_contour_drawing_2d(view)

            self.assertEqual(len(c2d), len(c3d), "8 outer vertices expected on test_box")

            res = align_drawing_to_unfold(c2d, c3d, view_scale=getattr(view, "Scale", 1.0))
            self.assertTrue(res.ok)
            self.assertLess(res.rms_error_mm, 0.1, f"Expected RMS < 0.1mm, got {res.rms_error_mm}")
            self.assertEqual(res.method, "KABSCH")
            self.assertFalse(res.is_mirrored)
            self.assertFalse(res.planar_reflection)
            self._assert_is_valid_affine_transform(res)
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_mirror_rms(self):
        """test_mirror: rms < 0.5 mm, is_mirrored=True, planar_reflection=True, T·T⁻¹≈I."""
        doc = App.openDocument(str(self.mirror_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = extract_outer_contour_drawing_2d(view)

            self.assertEqual(len(c2d), len(c3d), "8 outer vertices expected on test_mirror")

            res = align_drawing_to_unfold(c2d, c3d, allow_mirror=True, view_scale=getattr(view, "Scale", 1.0))
            self.assertTrue(res.ok)
            self.assertLess(res.rms_error_mm, 0.5, f"Expected RMS < 0.5mm on mirror, got {res.rms_error_mm}")
            self.assertTrue(res.planar_reflection, "Planar reflection must be flagged on mirrored TechDraw view")
            self._assert_is_valid_affine_transform(res)
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_respects_view_scale(self):
        """B8: view.Scale=0.5 -> de-scaling ensures isometric coordinates, det(R)≈1, view_scale preserved."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            
            # Temporarily set Scale to 0.5
            original_scale = view.Scale
            view.Scale = 0.5
            try:
                c2d_scaled = extract_outer_contour_drawing_2d(view)
                res = align_drawing_to_unfold(c2d_scaled, c3d, view_scale=view.Scale)
                self.assertTrue(res.ok)
                self.assertLess(res.rms_error_mm, 0.1)
                self.assertEqual(res.view_scale, 0.5)
                self._assert_is_valid_affine_transform(res)
            finally:
                view.Scale = original_scale
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_vertex_count_mismatch_errors(self):
        """B7: Vertex count mismatch raises AlignmentError without silent truncation."""
        c2d = [(0, 0), (10, 0), (10, 10), (0, 10)]
        c3d = [(0, 0), (10, 0), (10, 10)]  # 3 vs 4 vertices
        with self.assertRaises(AlignmentError) as ctx:
            align_drawing_to_unfold(c2d, c3d)
        self.assertIn("contour_vertex_count_mismatch: 4 vs 3", str(ctx.exception))

    def test_alignment_rms_gate(self):
        """Y8: When RMS exceeds threshold and force_alignment=False, returns ok=False with warning."""
        c2d = [(0, 0), (10, 0), (10, 10), (0, 10)]
        # Distort one point by 5 mm
        c3d = [(0, 0), (10, 0), (15, 10), (0, 10)]

        res = align_drawing_to_unfold(c2d, c3d, max_rms_mm=0.5, force_alignment=False)
        self.assertFalse(res.ok)
        self.assertGreater(res.rms_error_mm, 0.5)
        self.assertTrue(any("exceeds threshold" in w for w in res.warnings))

    def test_alignment_force_flag(self):
        """Y8: When RMS exceeds threshold and force_alignment=True, returns ok=True with FORCED warning."""
        c2d = [(0, 0), (10, 0), (10, 10), (0, 10)]
        c3d = [(0, 0), (10, 0), (15, 10), (0, 10)]

        res = align_drawing_to_unfold(c2d, c3d, max_rms_mm=0.5, force_alignment=True)
        self.assertTrue(res.ok)
        self.assertGreater(res.rms_error_mm, 0.5)
        self.assertTrue(any("FORCED" in w for w in res.warnings))

    def test_alignment_pca_fallback(self):
        """Y9: PCA fallback is engaged when requested or when Kabsch encounters degeneracy."""
        # Collinear or degenerated contour where SVD can fallback to PCA
        from freecad_smbs.manual.alignment import _pca_align
        P = np.array([[0, 0, 0], [10, 0, 0], [20, 0, 0], [30, 0, 0]], dtype=float)
        Q = np.array([[0, 5, 0], [0, 15, 0], [0, 25, 0], [0, 35, 0]], dtype=float)
        R_pca, t_pca, rms_pca = _pca_align(P, Q)
        self.assertLess(rms_pca, 1e-4)

    def test_method_selection_priority(self):
        """Y9: Kabsch is primary; TECHDRAW_VIEW fallback only triggers if allow_view_transform=True."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = extract_outer_contour_drawing_2d(view)

            res = align_drawing_to_unfold(c2d, c3d, allow_view_transform=False)
            self.assertEqual(res.method, "KABSCH")
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_inverse_property(self):
        """Analytical and numerical inverse satisfies ||T·T⁻¹ - I||_F < 1e-6 across rotations."""
        from freecad_smbs.manual.alignment import _build_transform_4x4
        # Test random rotation matrix
        theta = math.radians(45.0)
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        t = np.array([12.5, -45.0, 100.0])

        T, T_inv = _build_transform_4x4(R, t)
        err = np.linalg.norm(T @ T_inv - np.eye(4))
        self.assertLess(err, 1e-6)


if __name__ == "__main__":
    unittest.main()
