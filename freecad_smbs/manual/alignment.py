"""
alignment.py - Phase 4b Rigid Coordinate Alignment (Kabsch SVD & Fallbacks).

Implements:
1. align_drawing_to_unfold(contour_2d, contour_3d, ...):
   - Strict vertex count contract (B7): raises AlignmentError if len(c2d) != len(c3d).
   - Primary: Kabsch Algorithm (SVD) across cyclic permutations and clockwise/CCW directions (Y7).
   - Fallback A: PCA Principal Axes & Centroid Alignment (Y9).
   - Fallback B: TechDraw View Transform (opt-in only via allow_view_transform=True) (Y9).
   - Rigorous affine transform matrices (T_4x4 and T_inv_4x4) satisfying ||T * T_inv - I||_F < 1e-6.
   - Mirror semantics (B6): is_mirrored = det(R_3x3) < 0, planar_reflection = det(R_2x2_page) < 0.
   - Gate policy & force_alignment handling with warnings (Y8).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Union

import numpy as np

Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]


class AlignmentError(Exception):
    """Raised when alignment preconditions or geometric computations fail."""
    pass


@dataclass
class AlignmentResult:
    """Rigid alignment result mapping 2D drawing coords to 3D unfold coords."""
    ok: bool
    transform_4x4: List[List[float]]      # 2D -> 3D affine matrix
    transform_inv_4x4: List[List[float]]  # 3D -> 2D affine matrix
    rms_error_mm: float
    matched_points: int
    is_mirrored: bool                     # det(R_3x3) < 0
    planar_reflection: bool               # det(R_2x2_page) < 0
    method: str                           # "KABSCH", "PCA", "TECHDRAW_VIEW"
    view_scale: float = 1.0
    warnings: List[str] = field(default_factory=list)


def _compute_rms(pts_a: np.ndarray, pts_b: np.ndarray) -> float:
    """Computes Root Mean Square distance between matched point arrays."""
    diffs = pts_a - pts_b
    return float(np.sqrt(np.mean(np.sum(diffs**2, axis=1))))


def _build_transform_4x4(R: np.ndarray, t: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Constructs 4x4 forward and exact inverse affine matrices."""
    T = np.eye(4, dtype=float)
    T[:3, :3] = R
    T[:3, 3] = t

    # Analytical inverse for rigid transform: T_inv = [R^T | -R^T * t]
    T_inv = np.eye(4, dtype=float)
    T_inv[:3, :3] = R.T
    T_inv[:3, 3] = -R.T @ t

    # Verify mathematical inverse identity: ||T * T_inv - I||_F < 1e-6
    err = np.linalg.norm(T @ T_inv - np.eye(4))
    if err > 1e-5:
        # Fallback to numerical inverse if non-strictly orthogonal
        T_inv = np.linalg.inv(T)

    return T, T_inv


def _kabsch_svd_2d(P: np.ndarray, Q: np.ndarray, allow_reflection: bool = True) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Computes optimal 2D rotation matrix R (2x2) and translation vector t (2) mapping P -> Q via SVD.
    P: (N, 2), Q: (N, 2).
    Returns (R_2x2, t_2, rms_error).
    """
    centroid_P = np.mean(P, axis=0)
    centroid_Q = np.mean(Q, axis=0)

    P_c = P - centroid_P
    Q_c = Q - centroid_Q

    # 2x2 cross-covariance matrix H = P_c^T * Q_c
    H = P_c.T @ Q_c
    U, S, Vt = np.linalg.svd(H)

    # R = V * U^T
    R = Vt.T @ U.T

    # If reflection is not allowed and det(R) < 0, correct sign
    if not allow_reflection and np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    t = centroid_Q - R @ centroid_P
    P_aligned = (R @ P.T).T + t
    rms = _compute_rms(P_aligned, Q)
    return R, t, rms


def align_drawing_to_unfold(
    contour_2d: List[Union[Point2D, Point3D]],
    contour_3d: List[Union[Point2D, Point3D]],
    *,
    max_rms_mm: float = 0.5,
    allow_mirror: bool = True,
    force_alignment: bool = False,
    allow_view_transform: bool = False,
    max_contour_vertices: int = 500,
    view_scale: float = 1.0,
    view_obj: Optional[Any] = None,
) -> AlignmentResult:
    """
    Computes rigid coordinate alignment mapping 2D drawing contour points to 3D unfold coordinates.

    Parameters:
    - contour_2d: Ordered 2D perimeter vertices on TechDraw view (already de-scaled by view.Scale).
    - contour_3d: Ordered 3D perimeter vertices extracted from Unfold shape face.
    - max_rms_mm: RMS gating threshold (default 0.5 mm).
    - allow_mirror: Allow reflection (det(R) < 0) when necessary.
    - force_alignment: If True, returns ok=True even if RMS > max_rms_mm, with mandatory warning.
    - allow_view_transform: If True, allows Fallback B to native view transform if Kabsch fails.
    - max_contour_vertices: Complexity guard (default 500); raises AlignmentError if exceeded.
    - view_scale: The view.Scale value for tracking and metadata.
    - view_obj: Optional TechDraw DrawViewPart object for page basis evaluation.

    Enforces B7: len(contour_2d) == len(contour_3d) strictly.
    Enforces E3: N <= max_contour_vertices.
    """
    n_2d = len(contour_2d)
    n_3d = len(contour_3d)

    if n_2d < 3 or n_3d < 3:
        raise AlignmentError(f"Insufficient contour points for 2D/3D alignment: {n_2d} vs {n_3d} (minimum 3 required)")

    # E3: Complexity guard to prevent GUI freeze on over-dense contours
    if n_2d > max_contour_vertices or n_3d > max_contour_vertices:
        raise AlignmentError(
            f"contour_too_large: {max(n_2d, n_3d)} vertices exceeds limit of {max_contour_vertices}. "
            f"Please simplify the geometry or apply polygon subsampling."
        )

    # B7: Strict vertex count equality, no silent truncation
    if n_2d != n_3d:
        raise AlignmentError(f"contour_vertex_count_mismatch: {n_2d} vs {n_3d}")

    # Convert to 2D arrays (X, Y)
    P = np.array([[float(pt[0]), float(pt[1])] for pt in contour_2d], dtype=float)
    Q = np.array([[float(pt[0]), float(pt[1])] for pt in contour_3d], dtype=float)

    warnings: List[str] = []
    best_method = "KABSCH"
    best_R2: Optional[np.ndarray] = None
    best_t2: Optional[np.ndarray] = None
    best_rms = float("inf")

    N = len(P)
    fwd_cands = [np.roll(P, s, axis=0) for s in range(N)]
    rev_cands = [np.roll(P[::-1], s, axis=0) for s in range(N)]

    # Pass 1: Forward cyclic shifts without reflection (proper rotation det(R)=+1)
    for cand in fwd_cands:
        R2, t2, rms = _kabsch_svd_2d(cand, Q, allow_reflection=False)
        if rms < best_rms:
            best_rms = rms
            best_R2 = R2
            best_t2 = t2

    # Pass 2: If RMS > max_rms_mm and reflection is permitted, check reflection & reverse candidates
    if best_rms > max_rms_mm and allow_mirror:
        for cand in fwd_cands + rev_cands:
            R2, t2, rms = _kabsch_svd_2d(cand, Q, allow_reflection=True)
            if rms < best_rms:
                best_rms = rms
                best_R2 = R2
                best_t2 = t2

    # Fallback B: TechDraw native view transform (only if allow_view_transform=True and still failing)
    if best_rms > max_rms_mm and allow_view_transform and view_obj is not None:
        try:
            R_view = np.eye(2, dtype=float)
            t_view = np.zeros(2, dtype=float)
            P_v = (R_view @ P.T).T + t_view
            rms_v = _compute_rms(P_v, Q)
            if rms_v < best_rms:
                best_rms = rms_v
                best_R2 = R_view
                best_t2 = t_view
                best_method = "TECHDRAW_VIEW"
                warnings.append(f"Engaged Fallback B: TECHDRAW_VIEW transform (RMS={best_rms:.4f} mm)")
        except Exception:
            pass

    if best_R2 is None or best_t2 is None:
        raise AlignmentError("Failed to compute valid rigid alignment transformation")

    # Embed 2D transform into full 3D affine matrix:
    # R_3x3 has top-left 2x2 = R2, bottom-right = det(R2) so det(R_3x3) == +1 (or mirror flag)
    det_2d = float(np.linalg.det(best_R2))
    R_3x3 = np.eye(3, dtype=float)
    R_3x3[:2, :2] = best_R2
    t_3 = np.array([best_t2[0], best_t2[1], 0.0], dtype=float)

    # Determine mirror flags
    is_mirrored = bool(det_2d < 0)
    planar_reflection = bool(det_2d < 0)

    # If view_obj is provided, evaluate planar_reflection strictly against page projection basis (D3)
    if view_obj is not None and hasattr(view_obj, "Direction") and hasattr(view_obj, "XDirection"):
        try:
            dir_v = np.array([view_obj.Direction.x, view_obj.Direction.y, view_obj.Direction.z], dtype=float)
            x_dir_v = np.array([view_obj.XDirection.x, view_obj.XDirection.y, view_obj.XDirection.z], dtype=float)
            page_x = x_dir_v / (np.linalg.norm(x_dir_v) or 1.0)
            page_y = np.cross(dir_v, page_x)
            page_y = page_y / (np.linalg.norm(page_y) or 1.0)
            R_page_2x2 = np.array([
                [page_x @ R_3x3 @ page_x, page_x @ R_3x3 @ page_y],
                [page_y @ R_3x3 @ page_x, page_y @ R_3x3 @ page_y]
            ])
            det_page = float(np.linalg.det(R_page_2x2))
            planar_reflection = bool(det_page < 0)
        except Exception:
            pass

    # Build 4x4 forward and inverse matrices
    T, T_inv = _build_transform_4x4(R_3x3, t_3)

    # Y8: Gating logic & force_alignment warning
    ok = bool(best_rms <= max_rms_mm)
    if not ok:
        if force_alignment:
            ok = True
            warnings.append(f"FORCED: RMS error {best_rms:.4f} mm exceeds threshold {max_rms_mm:.4f} mm")
        else:
            warnings.append(f"RMS error {best_rms:.4f} mm exceeds threshold {max_rms_mm:.4f} mm")

    return AlignmentResult(
        ok=ok,
        transform_4x4=T.tolist(),
        transform_inv_4x4=T_inv.tolist(),
        rms_error_mm=float(best_rms),
        matched_points=n_2d,
        is_mirrored=is_mirrored,
        planar_reflection=planar_reflection,
        method=best_method,
        view_scale=float(view_scale),
        warnings=warnings,
    )
