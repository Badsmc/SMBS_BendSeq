"""Continuous-in-parameter 2D translation screening by polygon swept area.

For a rigid polygon P translated along vector d, its swept area is exactly
P + [0,d]: the union of the starting polygon, ending polygon, and quadrilateral
strips swept by every polygon edge. This holds for concave simple polygons too.
Tool profiles are polygonal approximations and the check is ONE machine section,
not 3D safety. TOUCH/UNVERIFIED are never certified clear.
"""
from __future__ import annotations

from math import isfinite
from time import perf_counter

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactStatus, check_machine_section,
)


def _bbox(poly):
    return (min(x for x, _ in poly), min(y for _, y in poly),
            max(x for x, _ in poly), max(y for _, y in poly))


def _disjoint(a, b, tol):
    return (a[2] < b[0]-tol or b[2] < a[0]-tol
            or a[3] < b[1]-tol or b[3] < a[1]-tol)


def _translated(poly, dx, dz):
    return tuple((float(x)+dx, float(y)+dz) for x, y in poly)


def swept_translation_patches(polygon, dx, dz):
    """Yield polygons whose UNION equals the continuous translation sweep.

    Strip quads with zero area (edges parallel to travel) are omitted; the
    start/end polygons already cover their degenerate swept boundaries.
    """
    vertices = tuple((float(x), float(y)) for x, y in polygon)
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices = vertices[:-1]
    if len(vertices) < 3 or any(
        not isfinite(x) or not isfinite(y) for x, y in vertices
    ):
        raise ValueError("invalid sheet polygon")
    if not isfinite(dx) or not isfinite(dz):
        raise ValueError("non-finite translation")
    yield vertices
    if dx == 0 and dz == 0:
        return
    yield _translated(vertices, dx, dz)
    for i, a in enumerate(vertices):
        b = vertices[(i + 1) % len(vertices)]
        area2 = (b[0]-a[0])*dz - (b[1]-a[1])*dx
        if abs(area2) <= 1e-10:
            continue
        yield (a, b, (b[0]+dx, b[1]+dz), (a[0]+dx, a[1]+dz))


def screen_swept_translation(
    polygons, punch, die, *, dx=0.0, dz=0.0, tolerance_mm=0.02,
    deadline_monotonic=None,
):
    """Check all strip components of exact planar rigid translation sweep.

    A CLEAR result excludes intersections of these polygonal shapes and
    stationary tools at ANY translation fraction, not just sampled poses.
    The tool/sheet cross-section approximation and oracle uncertainty remain.
    """
    if punch is None or die is None or not polygons:
        return {"status": "UNVERIFIED_2D", "reason": "missing_geometry",
                "checked_patches": 0}
    if (not isfinite(tolerance_mm) or
            not 1e-5 <= tolerance_mm <= 1.0):
        raise ValueError("invalid contact tolerance")
    try:
        tool_boxes = (_bbox(punch[0]), _bbox(die[0]))
    except (TypeError, IndexError, ValueError):
        return {"status": "UNVERIFIED_2D", "reason": "invalid_tool_contours",
                "checked_patches": 0}
    count = 0
    for component, polygon in polygons:
        try:
            patches = swept_translation_patches(polygon, dx, dz)
            for patch in patches:
                if deadline_monotonic is not None and perf_counter() >= deadline_monotonic:
                    return {"status": "UNVERIFIED_SEARCH_LIMIT_REACHED",
                            "reason": "deadline_during_swept_contour",
                            "checked_patches": count}
                if all(_disjoint(_bbox(patch), box, tolerance_mm)
                       for box in tool_boxes):
                    continue
                count += 1
                report = check_machine_section(
                    ((component, patch),), punch, die, tolerance_mm
                )
                if report.status is not ContactStatus.CLEAR:
                    issue = next(
                        (finding for finding in report.findings
                         if finding.status is report.status),
                        None,
                    )
                    return {
                        "status": ("SWEPT_2D_INTERFERENCE" if report.status is ContactStatus.INTERFERENCE
                                   else "UNVERIFIED_SWEPT_2D_CONTACT"),
                        "reason": issue.reason if issue else "ambiguous_contact",
                        "tool": issue.tool if issue else None,
                        "component": component,
                        "patch_index": count,
                        "contact_status": report.status.value,
                        "checked_patches": count,
                    }
        except (ValueError, TypeError, IndexError) as exc:
            return {"status": "UNVERIFIED_2D",
                    "reason": type(exc).__name__ + ": " + str(exc),
                    "checked_patches": count}
    return {
        "status": "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED",
        "reason": "continuous_rigid_translation_of_polygonal_2d_section",
        "checked_patches": count,
    }


def screen_lift_then_withdraw_swept(
    polygons, punch, die, *, lift_mm, distance_mm, direction,
    tolerance_mm=0.02, deadline_monotonic=None,
):
    """Check two continuous planar legs without intermediate discretization."""
    if direction not in (-1, 1):
        raise ValueError("invalid direction")
    if (not isfinite(lift_mm) or lift_mm <= 0 or
            not isfinite(distance_mm) or distance_mm <= 0):
        raise ValueError("invalid path dimensions")
    lift = screen_swept_translation(
        polygons, punch, die, dz=lift_mm,
        tolerance_mm=tolerance_mm, deadline_monotonic=deadline_monotonic,
    )
    if lift["status"] != "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED":
        return {"status": lift["status"], "phase": "LIFT",
                "first_problem": lift}
    moved = tuple(
        (name, _translated(poly, 0.0, lift_mm)) for name, poly in polygons
    )
    lateral = screen_swept_translation(
        moved, punch, die, dx=direction*distance_mm,
        tolerance_mm=tolerance_mm, deadline_monotonic=deadline_monotonic,
    )
    return {
        "status": lateral["status"],
        "phase": ("LATERAL_AFTER_LIFT" if
                  lateral["status"] != "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED"
                  else "COMPLETE"),
        "first_problem": (
            lateral if lateral["status"] != "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED"
            else None
        ),
        "checked_patches": lift["checked_patches"] + lateral["checked_patches"],
    }
