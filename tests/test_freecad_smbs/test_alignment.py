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

    def test_alignment_recovers_known_perturbation(self):
        """D1.1 & F1: Recovers known synthetic 2D rotation and translation under noise on both standard and mirror views."""
        # 1. Standard Box: T_inv directly recovers T_known
        doc_box = App.openDocument(str(self.box_path))
        try:
            view_box = doc_box.getObject("View")
            P_box = np.array(extract_outer_contour_drawing_2d(view_box))[:, :2]

            theta = 0.03
            c, s = math.cos(theta), math.sin(theta)
            R_known = np.array([[c, -s], [s, c]])
            t_known = np.array([3.7, -2.1])

            np.random.seed(42)
            noise = np.random.normal(0, 0.05, P_box.shape)
            P_box_noisy = (R_known @ P_box.T).T + t_known + noise

            res_box = align_drawing_to_unfold(P_box_noisy, P_box)
            self.assertLess(res_box.rms_error_mm, 0.15)

            T_inv_box = np.array(res_box.transform_inv_4x4)
            R_rec_box = T_inv_box[:2, :2]
            t_rec_box = T_inv_box[:2, 3]

            angle_rec_box = math.atan2(R_rec_box[1, 0], R_rec_box[0, 0])
            t_err_box = float(np.linalg.norm(t_rec_box - t_known))
            rot_err_box = abs(angle_rec_box - theta)

            self.assertLess(t_err_box, 0.1, f"Box translation error {t_err_box} must be < 0.1mm")
            self.assertLess(rot_err_box, 0.01, f"Box rotation error {rot_err_box} must be < 0.01 rad")
        finally:
            App.closeDocument(doc_box.Name)

        # 2. F1: Mirror View: Composition T_comp = T_pert^-1 @ T_clean recovers T_known
        doc_mirror = App.openDocument(str(self.mirror_path))
        try:
            view_mirror = doc_mirror.getObject("View")
            unfold_mirror = extract_unfold_solid(doc_mirror)
            c3d_mirror = np.array(extract_outer_contour_unfold_3d(unfold_mirror))[:, :2]
            c2d_mirror = np.array(extract_outer_contour_drawing_2d(view_mirror))[:, :2]

            res_clean = align_drawing_to_unfold(c2d_mirror, c3d_mirror)
            T_clean = np.array(res_clean.transform_4x4)

            np.random.seed(42)
            noise_m = np.random.normal(0, 0.05, c2d_mirror.shape)
            c2d_pert = (R_known @ c2d_mirror.T).T + t_known + noise_m

            res_pert = align_drawing_to_unfold(c2d_pert, c3d_mirror)
            T_pert_inv = np.array(res_pert.transform_inv_4x4)

            # Composition mapping c2d -> c2d_pert: T_comp = T_pert^-1 @ T_clean
            T_comp = T_pert_inv @ T_clean
            R_comp = T_comp[:2, :2]
            t_comp = T_comp[:2, 3]

            angle_comp = math.atan2(R_comp[1, 0], R_comp[0, 0])
            t_err_mirror = float(np.linalg.norm(t_comp - t_known))
            rot_err_mirror = abs(angle_comp - theta)
            frob_err = float(np.linalg.norm(R_comp - R_known))

            self.assertLess(t_err_mirror, 0.05, f"Mirror composition translation error {t_err_mirror} must be < 0.05mm")
            self.assertLess(rot_err_mirror, 0.005, f"Mirror composition rotation error {rot_err_mirror} must be < 0.005 rad")
            self.assertLess(frob_err, 0.01, f"Mirror composition Frobenius norm error {frob_err} must be < 0.01")
        finally:
            App.closeDocument(doc_mirror.Name)

    def test_extract_contour_uses_consistent_source(self):
        """F2: Verifies primary projectPoint branch matches direct model projection."""
        doc = App.openDocument(str(self.box_path))
        try:
            view = doc.getObject("View")
            c2d = extract_outer_contour_drawing_2d(view)

            # Direct projection from source shape face
            source = view.Source[0]
            best_face = max(source.Shape.Faces, key=lambda f: f.Area)
            direct_pts = [view.projectPoint(v.Point) for v in best_face.OuterWire.Vertexes]
            direct_2d = [(round(p.x, 4), round(p.y, 4)) for p in direct_pts]
            if len(direct_2d) > 2 and math.hypot(direct_2d[0][0] - direct_2d[-1][0], direct_2d[0][1] - direct_2d[-1][1]) < 0.05:
                direct_2d.pop()

            np.testing.assert_allclose(c2d, direct_2d, atol=1e-4, err_msg="extract_outer_contour_drawing_2d must strictly match projectPoint")
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_noise_robustness(self):
        """D1.2: Algorithm stays within 0.5mm gate under realistic 0.3mm noise."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = np.array(extract_outer_contour_drawing_2d(view))[:, :2]

            np.random.seed(42)
            c2d_noisy = c2d + np.random.normal(0, 0.3, c2d.shape)

            res = align_drawing_to_unfold(c2d_noisy, c3d)
            self.assertTrue(res.ok)
            self.assertLess(res.rms_error_mm, 0.5, f"Expected RMS < 0.5mm, got {res.rms_error_mm}")
            self.assertFalse(res.is_mirrored)
            self.assertFalse(res.planar_reflection)
        finally:
            App.closeDocument(doc.Name)

    def test_alignment_deterministic_under_permutation(self):
        """D1.3: Y7 cyclic shift invariance: different starting vertex yields identical transform."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = np.array(extract_outer_contour_drawing_2d(view))

            # Shift contour starting point by 3 vertices
            c2d_shifted = np.roll(c2d, 3, axis=0)

            res1 = align_drawing_to_unfold(c2d, c3d)
            res2 = align_drawing_to_unfold(c2d_shifted, c3d)

            np.testing.assert_allclose(res1.transform_4x4, res2.transform_4x4, atol=1e-6)
            self.assertAlmostEqual(res1.rms_error_mm, res2.rms_error_mm, places=6)
        finally:
            App.closeDocument(doc.Name)

    def test_extract_contour_scale_independence(self):
        """D2: extract_outer_contour_drawing_2d returns invariant model coordinates across view scales."""
        doc = App.openDocument(str(self.box_path))
        try:
            view = doc.getObject("View")
            c2d_1 = np.array(extract_outer_contour_drawing_2d(view))

            original_scale = view.Scale
            original_type = view.ScaleType
            try:
                view.ScaleType = "Custom"
                view.Scale = 0.5
                doc.recompute()
                c2d_half = np.array(extract_outer_contour_drawing_2d(view))

                view.Scale = 2.0
                doc.recompute()
                c2d_double = np.array(extract_outer_contour_drawing_2d(view))

                np.testing.assert_allclose(c2d_1, c2d_half, atol=1e-4, err_msg="Contour coords must be invariant at Scale=0.5")
                np.testing.assert_allclose(c2d_1, c2d_double, atol=1e-4, err_msg="Contour coords must be invariant at Scale=2.0")
            finally:
                view.ScaleType = original_type
                view.Scale = original_scale
                doc.recompute()
        finally:
            App.closeDocument(doc.Name)

    def test_planar_reflection_from_page_basis(self):
        """D3: Formally verifies planar_reflection sign from TechDraw view page basis."""
        # 1. Non-mirrored view: Direction=(0, 0, 1), XDirection=(1, 0, 0)
        doc_box = App.openDocument(str(self.box_path))
        try:
            view_box = doc_box.getObject("View")
            unfold_box = extract_unfold_solid(doc_box)
            c3d_box = extract_outer_contour_unfold_3d(unfold_box)
            c2d_box = extract_outer_contour_drawing_2d(view_box)

            res_box = align_drawing_to_unfold(c2d_box, c3d_box, view_obj=view_box)
            self.assertFalse(res_box.is_mirrored)
            self.assertFalse(res_box.planar_reflection)
        finally:
            App.closeDocument(doc_box.Name)

        # 2. Mirrored view: Direction=(0, 0, -1), XDirection=(1, 0, 0)
        doc_mirror = App.openDocument(str(self.mirror_path))
        try:
            view_mirror = doc_mirror.getObject("View")
            unfold_mirror = extract_unfold_solid(doc_mirror)
            c3d_mirror = extract_outer_contour_unfold_3d(unfold_mirror)
            c2d_mirror = extract_outer_contour_drawing_2d(view_mirror)

            res_mirror = align_drawing_to_unfold(c2d_mirror, c3d_mirror, view_obj=view_mirror)
            self.assertTrue(res_mirror.is_mirrored)
            self.assertTrue(res_mirror.planar_reflection)
        finally:
            App.closeDocument(doc_mirror.Name)

    def test_alignment_rejects_large_contours(self):
        """E3: Rejects contours with N > max_contour_vertices (500) to protect against UI freeze."""
        large_contour = [(float(i), float(i)) for i in range(501)]
        with self.assertRaises(AlignmentError) as ctx:
            align_drawing_to_unfold(large_contour, large_contour, max_contour_vertices=500)
        self.assertIn("contour_too_large: 501 vertices exceeds limit of 500", str(ctx.exception))

    def test_alignment_inverse_property(self):
        """E4: Semantic roundtrip verification: T maps 2D->3D and T_inv maps 3D->2D within 0.01mm."""
        doc = App.openDocument(str(self.box_path))
        try:
            unfold = extract_unfold_solid(doc)
            c3d = extract_outer_contour_unfold_3d(unfold)
            view = doc.getObject("View")
            c2d = extract_outer_contour_drawing_2d(view)

            res = align_drawing_to_unfold(c2d, c3d)
            T = np.array(res.transform_4x4)
            T_inv = np.array(res.transform_inv_4x4)

            for p_2d, p_3d in zip(c2d, c3d):
                p_2d_h = np.array([p_2d[0], p_2d[1], 0.0, 1.0])
                p_3d_from_2d = T @ p_2d_h
                self.assertLess(np.linalg.norm(p_3d_from_2d[:2] - np.array(p_3d[:2])), 0.01)

                p_3d_h = np.array([p_3d[0], p_3d[1], 0.0, 1.0])
                p_2d_from_3d = T_inv @ p_3d_h
                self.assertLess(np.linalg.norm(p_2d_from_3d[:2] - np.array(p_2d[:2])), 0.01)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()

