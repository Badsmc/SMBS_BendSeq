"""WorkpiecePlacement3D - Spatial 3D Placement of SheetWorkpiece in Press Brake Machine Coordinates.

Architecture Contract:
1. Coordinates:
   - Part Space (x, y, z): Authoritative geometry coordinates of unfolded/folded sheet in FreeCAD.
   - Panel Space: Position of each rigid panel derived strictly from FoldState.transforms[panel_id].
   - Active Bend Line: Directed line in 3D along which the active punch and die operate.
   - Machine Space (X_m, Y_m, Z_m):
     * X_m: Along press brake tooling length (coaxial with bend line).
     * Y_m: Horizontal in cross-section plane (depth of bed, positive towards backgauge / operator).
     * Z_m: Vertical along ram motion (positive upwards towards punch ram, negative towards lower die).
2. Placement:
   - Rigid 3D isometry (det(R) = +1.0) mapping Part Space -> Machine Space.
   - Never alters FoldState angles or PanelGraph topology (single source of truth).
   - Maps active bend line strictly onto X_m axis (Y_m = 0, Z_m = 0 at die reference).
   - For non-parallel consecutive bends: performs spatial 3D rotation in horizontal (X_m, Y_m) plane
     and/or physical turnover around X_m/Y_m, ensuring identical physical points remain continuous.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from smbs_core.kinematics2d.panel_kinematics import FoldState, RigidTransform


Point3D = Tuple[float, float, float]
Matrix3x3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def _vec_norm(v: Point3D) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _vec_normalize(v: Point3D, eps: float = 1e-12) -> Point3D:
    n = _vec_norm(v)
    if n < eps:
        raise ValueError(f"Cannot normalize degenerate vector {v}")
    return (v[0] / n, v[1] / n, v[2] / n)


def _vec_dot(a: Point3D, b: Point3D) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vec_cross(a: Point3D, b: Point3D) -> Point3D:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _mat_mult(A: Matrix3x3, B: Matrix3x3) -> Matrix3x3:
    return tuple(
        tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )  # type: ignore[return-value]


def _mat_vec(R: Matrix3x3, v: Point3D) -> Point3D:
    return (
        R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2],
        R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2],
        R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2],
    )


def _mat_transpose(R: Matrix3x3) -> Matrix3x3:
    return (
        (R[0][0], R[1][0], R[2][0]),
        (R[0][1], R[1][1], R[2][1]),
        (R[0][2], R[1][2], R[2][2]),
    )


def _mat_det(R: Matrix3x3) -> float:
    return (
        R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
        - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
        + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
    )


@dataclass(frozen=True)
class PlacementTransform3D:
    """Rigid 3D transformation `P_m = R * P_part + T` mapping Part Space -> Machine Space."""
    rotation: Matrix3x3
    translation: Point3D

    def apply(self, point: Point3D) -> Point3D:
        p_rot = _mat_vec(self.rotation, point)
        return (
            p_rot[0] + self.translation[0],
            p_rot[1] + self.translation[1],
            p_rot[2] + self.translation[2],
        )

    def inverse(self) -> PlacementTransform3D:
        r_inv = _mat_transpose(self.rotation)
        t_inv = _mat_vec(r_inv, (-self.translation[0], -self.translation[1], -self.translation[2]))
        return PlacementTransform3D(rotation=r_inv, translation=t_inv)

    def then(self, after: PlacementTransform3D) -> PlacementTransform3D:
        r_comp = _mat_mult(after.rotation, self.rotation)
        t_rot = _mat_vec(after.rotation, self.translation)
        t_comp = (
            t_rot[0] + after.translation[0],
            t_rot[1] + after.translation[1],
            t_rot[2] + after.translation[2],
        )
        return PlacementTransform3D(rotation=r_comp, translation=t_comp)


class WorkpiecePlacement3D:
    """Spatial 3D placement solver for sheet workpieces in press brake machines.

    Maintains coordinate transformation from authoritative FoldState 3D coordinates
    to Machine Space (X_m, Y_m, Z_m) for ANY active bend, including nonparallel axes.
    """

    @staticmethod
    def get_world_bend_axis(fold_state: FoldState, bend_id: str) -> Tuple[Point3D, Point3D, Point3D]:
        """Compute the 3D world line of `bend_id` after all prior folded operations.

        Returns:
            (pt_start, pt_end, unit_direction) in authoritative Part 3D Space.
        """
        if bend_id not in fold_state.bends:
            raise KeyError(f"Unknown bend: {bend_id}")
        bend = fold_state.bends[bend_id]
        p_tf = fold_state.transforms[bend.fixed_panel]

        # Use 3D axis_xyz on bend edge
        p0_local, p1_local = bend.axis_xyz
        p0_world = p_tf.point(p0_local)
        p1_world = p_tf.point(p1_local)

        d = (p1_world[0] - p0_world[0], p1_world[1] - p0_world[1], p1_world[2] - p0_world[2])
        unit_dir = _vec_normalize(d)
        return p0_world, p1_world, unit_dir

    @staticmethod
    def compute_placement(
        fold_state: FoldState,
        active_bend_id: str,
        desired_midpoint_xm: float = 0.0,
        support_plane_zm: float = 0.0,
        align_bisector: bool = False,
    ) -> PlacementTransform3D:
        """Compute rigid 3D placement mapping Part Space -> Machine Space for `active_bend_id`.

        Guarantees:
        - Active bend line aligns with Machine X_m axis `(1.0, 0.0, 0.0)`.
        - Midpoint of active bend line is placed at `X_m = desired_midpoint_xm`.
        - Active bend line rests at `Y_m = 0.0, Z_m = support_plane_zm`.
        - When `align_bisector=True`, the bend angle bisector aligns with Machine +Z_m
          for symmetric flange rotation during V-die air-bending.
        - Proper right-handed rigid transform: det(R) = +1.0.
        """
        p0_world, p1_world, u_axis = WorkpiecePlacement3D.get_world_bend_axis(fold_state, active_bend_id)
        mid_world = (
            0.5 * (p0_world[0] + p1_world[0]),
            0.5 * (p0_world[1] + p1_world[1]),
            0.5 * (p0_world[2] + p1_world[2]),
        )

        bend = fold_state.bends[active_bend_id]
        p_fixed = fold_state.transforms[bend.fixed_panel]
        p_moving = fold_state.transforms[bend.moving_panel]

        # In unfolded/flat state panel normal is Z = (0, 0, 1)
        fixed_norm_world = _mat_vec(p_fixed.rotation, (0.0, 0.0, 1.0))
        moving_norm_world = _mat_vec(p_moving.rotation, (0.0, 0.0, 1.0))

        if align_bisector:
            # Bisector between adjacent fixed and moving panels.
            # When bend angle is non-zero, aligning the bisector with Machine Z_m
            # produces symmetric upward rise for both flanges in the V-die.
            sum_norm = (
                fixed_norm_world[0] + moving_norm_world[0],
                fixed_norm_world[1] + moving_norm_world[1],
                fixed_norm_world[2] + moving_norm_world[2],
            )
            norm_len = _vec_norm(sum_norm)
            if norm_len < 1e-6:
                n_ref = fixed_norm_world
            else:
                n_ref = (sum_norm[0] / norm_len, sum_norm[1] / norm_len, sum_norm[2] / norm_len)
        else:
            n_ref = fixed_norm_world

        # Cross-section transverse direction: v_cross = n_ref x u_axis
        v_cross = _vec_cross(n_ref, u_axis)
        if _vec_norm(v_cross) < 1e-6:
            # Fallback if axis is parallel to normal (should not occur in SheetMetal)
            temp = (0.0, 1.0, 0.0) if abs(u_axis[1]) < 0.9 else (1.0, 0.0, 0.0)
            v_cross = _vec_cross(temp, u_axis)
        v_transverse = _vec_normalize(v_cross)

        # Recalculate true upward normal: n_true = u_axis x v_transverse
        n_true = _vec_normalize(_vec_cross(u_axis, v_transverse))

        # Target in Machine Space:
        # u_axis -> Machine X_m (1, 0, 0)
        # v_transverse -> Machine Y_m (0, 1, 0)
        # n_true -> Machine Z_m (0, 0, 1)
        # Therefore, R = [u_axis; v_transverse; n_true]
        R: Matrix3x3 = (
            (u_axis[0], u_axis[1], u_axis[2]),
            (v_transverse[0], v_transverse[1], v_transverse[2]),
            (n_true[0], n_true[1], n_true[2]),
        )

        # Ensure det(R) == +1.0
        det = _mat_det(R)
        if abs(det - 1.0) > 1e-4:
            raise ValueError(f"Placement rotation matrix has invalid determinant {det}")

        # Translation: mid_world maps to (desired_midpoint_xm, 0.0, support_plane_zm)
        mid_rot = _mat_vec(R, mid_world)
        T: Point3D = (
            desired_midpoint_xm - mid_rot[0],
            -mid_rot[1],
            support_plane_zm - mid_rot[2],
        )

        return PlacementTransform3D(rotation=R, translation=T)

    @staticmethod
    def get_panel_vertices_machine(
        fold_state: FoldState,
        placement: PlacementTransform3D,
        panel_id: str,
        panel_vertices_local_xy: Sequence[Tuple[float, float]],
    ) -> List[Point3D]:
        """Compute exact 3D Machine coordinates of all vertices of `panel_id`."""
        if panel_id not in fold_state.transforms:
            raise KeyError(f"Unknown panel: {panel_id}")
        p_tf = fold_state.transforms[panel_id]

        out: List[Point3D] = []
        for x, y in panel_vertices_local_xy:
            p_world = p_tf.point((x, y, 0.0))
            p_mach = placement.apply(p_world)
            out.append(p_mach)
        return out
