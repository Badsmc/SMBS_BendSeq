"""C1 — validate 3D placement against authoritative FoldState.

Pure Python. Placement checks certify mathematical coordinate consistency only;
NOT tooling support, accessibility, sheet-solid intersections or collisions.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from .workpiece_placement_3d import WorkpiecePlacement3D, _mat_det


class PlacementValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PlacementReport3D:
    bend_id: str
    max_axis_error_mm: float
    midpoint_error_mm: float
    panel_count: int
    vertex_count: int
    collision_validated: bool = False
    support_validated: bool = False


def validate_rigid_3d(pose, tol=1e-7):
    if not math.isfinite(tol) or tol <= 0:
        raise PlacementValidationError("Tolerance must be positive finite")
    R, t = pose.rotation, pose.translation
    if len(R) != 3 or any(len(row) != 3 for row in R) or len(t) != 3:
        raise PlacementValidationError("Expected 3x3 rotation and XYZ translation")
    if not all(math.isfinite(v) for row in R for v in row) or not all(math.isfinite(v) for v in t):
        raise PlacementValidationError("Non-finite pose")
    if abs(_mat_det(R) - 1.0) > tol:
        raise PlacementValidationError("Improper rotation: determinant != +1")
    for i in range(3):
        for j in range(3):
            dot = sum(R[i][k]*R[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > tol:
                raise PlacementValidationError("Rotation is not orthogonal")


def validate_workpiece_placement(
    state, bend_id, pose, panel_vertices_xy, *,
    machine_axis_midpoint=(0.0, 0.0, 0.0), tolerance_mm=1e-6,
):
    """Validate placement for all panels and the selected bend axis.

    Convention: machine tooling axis runs along X_m, with center at supplied
    machine_axis_midpoint. Panel XY vertices come from the authoritative
    unfolded sketch; each panel's FoldState transform is applied once.
    Neither support nor collision is established by these tests.
    """
    validate_rigid_3d(pose, tolerance_mm)
    if len(machine_axis_midpoint) != 3 or not all(math.isfinite(v) for v in machine_axis_midpoint):
        raise PlacementValidationError("Invalid machine axis midpoint")
    p0, p1, _ = WorkpiecePlacement3D.get_world_bend_axis(state, bend_id)
    a, b = pose.apply(p0), pose.apply(p1)
    middle = tuple((a[k] + b[k])/2 for k in range(3))
    midpoint_error = math.dist(middle, machine_axis_midpoint)
    axis_error = max(
        abs(a[k]-machine_axis_midpoint[k]) for k in (1, 2)
    )
    axis_error = max(axis_error, *(abs(b[k]-machine_axis_midpoint[k]) for k in (1,2)))
    if midpoint_error > tolerance_mm or axis_error > tolerance_mm:
        raise PlacementValidationError("Bend axis does not match machine X axis")
    # The placement solver's roll convention puts the reference surface of
    # the fixed panel towards +Z_m. This catches silent face flips while
    # leaving punch/die support explicitly unverified.
    bend = state.bends[bend_id]
    fixed_rotation = state.transforms[bend.fixed_panel].rotation
    part_normal = tuple(fixed_rotation[i][2] for i in range(3))
    machine_normal = tuple(
        sum(pose.rotation[i][j] * part_normal[j] for j in range(3))
        for i in range(3)
    )
    if math.dist(machine_normal, (0.0, 0.0, 1.0)) > tolerance_mm:
        raise PlacementValidationError(
            "Reference surface normal does not map to +Z_m"
        )
    if not panel_vertices_xy:
        raise PlacementValidationError("Missing panel vertex data")
    total = 0
    for panel_id in state.graph.panels:
        if panel_id not in panel_vertices_xy:
            raise PlacementValidationError("Missing panel " + panel_id)
        verts = panel_vertices_xy[panel_id]
        if len(verts) < 3:
            raise PlacementValidationError("Degenerate panel " + panel_id)
        for xy in verts:
            if len(xy) != 2 or not all(math.isfinite(v) for v in xy):
                raise PlacementValidationError("Invalid panel vertex")
        transformed = WorkpiecePlacement3D.get_panel_vertices_machine(state, pose, panel_id, verts)
        if any(not all(math.isfinite(v) for v in xyz) for xyz in transformed):
            raise PlacementValidationError("Invalid machine-space vertex")
        total += len(verts)
    return PlacementReport3D(bend_id, axis_error, midpoint_error, len(state.graph.panels), total)


def compare_native_placement_matrices(
    preview_pose, native_pose, *,
    translation_tolerance_mm=1e-5,
    rotation_tolerance=1e-8,
):
    """Compare *machine* placements, never camera-relative 2D coordinates.

    This is a mathematical consistency check, not evidence of clearance.
    The native pose must carry its real 3x3 rotation and XYZ translation.
    Missing, malformed or nonfinite numbers return UNVERIFIED, never MATCH.
    """
    if translation_tolerance_mm <= 0 or rotation_tolerance <= 0:
        raise ValueError("positive finite tolerances required")
    if not (math.isfinite(translation_tolerance_mm)
            and math.isfinite(rotation_tolerance)):
        raise ValueError("finite tolerances required")
    try:
        expected_R = preview_pose.rotation
        expected_t = preview_pose.translation
        actual_R = native_pose["rotation"]
        actual_t = native_pose["translation"]
        if (len(expected_R) != 3 or len(actual_R) != 3
                or any(len(row) != 3 for row in expected_R)
                or any(len(row) != 3 for row in actual_R)
                or len(expected_t) != 3 or len(actual_t) != 3):
            raise ValueError("invalid matrix dimensions")
        r_delta = max(abs(float(expected_R[i][j])-float(actual_R[i][j]))
                      for i in range(3) for j in range(3))
        t_delta = max(abs(float(expected_t[i])-float(actual_t[i]))
                      for i in range(3))
        if not (math.isfinite(r_delta) and math.isfinite(t_delta)):
            raise ValueError("nonfinite values")
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        return {"status": "UNVERIFIED", "reason": str(exc)}
    return {
        "status": ("MATCH" if r_delta <= rotation_tolerance
                   and t_delta <= translation_tolerance_mm
                   else "POSE_MISMATCH"),
        "max_rotation_delta": r_delta,
        "max_translation_delta_mm": t_delta,
    }
