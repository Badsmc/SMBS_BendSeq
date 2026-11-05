"""D6 FCL PoC: optional 3D collision backend, separate from SMBS production.

- Import FreeCAD MeshPart and python-fcl only when invoked, not at import time.
- Use actual FreeCAD Mesh.Topology vertices/faces to build FCL BVH.
- Status is fail-closed: contact counts as a blocked operation.
- Static queries do NOT certify intermediate swept motion.
- The experiment compares repeated FCL decisions with exact CAD box fixtures.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from time import perf_counter


class FCLStatus(str, Enum):
    CLEAR = "CLEAR"
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class FCLCheck:
    status: FCLStatus
    reason: str
    distance_mm: float | None
    elapsed_ms: float

    def to_dict(self):
        return {
            "status": self.status.value,
            "reason": self.reason,
            "distance_mm": self.distance_mm,
            "elapsed_ms": round(self.elapsed_ms, 6),
        }


def _require_runtime():
    try:
        import numpy as np
        import fcl
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "python-fcl or NumPy unavailable in this FreeCAD Python environment: "
            + str(exc)
        ) from exc
    return np, fcl


def fcl_from_freecad_mesh(mesh):
    """Convert a verified closed FreeCAD Mesh object into FCL CollisionObject.

    FreeCAD Mesh.Topology contains a tuple (vertices, triangle indices).
    No geometry is recomputed by the adapter, and no source mesh is mutated.
    """
    np, fcl = _require_runtime()
    if (mesh is None or not mesh.isSolid() or mesh.hasSelfIntersections()
            or int(mesh.CountFacets) <= 0):
        raise ValueError("Mesh must be closed, non-self-intersecting and nonempty")
    vertices, triangles = mesh.Topology
    xyz = np.asarray(vertices, dtype=np.float64)
    indices = np.asarray(triangles, dtype=np.int32)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) < 4:
        raise ValueError("Invalid FreeCAD mesh vertices")
    if indices.ndim != 2 or indices.shape[1] != 3 or len(indices) < 4:
        raise ValueError("Only triangulated FreeCAD meshes supported")
    if not np.all(np.isfinite(xyz)) or np.any(indices < 0) or np.any(indices >= len(xyz)):
        raise ValueError("Invalid vertex or triangle values")
    model = fcl.BVHModel()
    model.beginModel(int(len(xyz)), int(len(indices)))
    model.addSubModel(xyz, indices)
    model.endModel()
    return fcl.CollisionObject(model)


def fcl_from_shape(shape, *, linear_deflection_mm=0.1, angular_deflection_rad=0.25):
    """Tessellate with existing FreeCAD MeshPart helper and build FCL BVH."""
    from .mesh_collision_poc import mesh_from_shape
    return fcl_from_freecad_mesh(mesh_from_shape(
        shape, linear_deflection_mm=linear_deflection_mm,
        angular_deflection_rad=angular_deflection_rad,
    ))


def check_static_pair(obj_a, obj_b, *, minimum_clearance_mm=0.02):
    """Collision = blocked (including valid physical contact).

    A disjoint shape with FCL minimum distance greater than the required
    clearance is CLEAR. Zero/nearly-zero/negative or unsupported distance
    is UNVERIFIED if collision query did not establish contact.
    """
    start = perf_counter()
    def emit(status, reason, distance=None):
        return FCLCheck(status, reason, distance, (perf_counter()-start)*1000)
    try:
        if not isfinite(minimum_clearance_mm) or minimum_clearance_mm < 0:
            return emit(FCLStatus.UNVERIFIED, "invalid_clearance")
        _, fcl = _require_runtime()
        result = fcl.CollisionResult()
        count = fcl.collide(obj_a, obj_b, fcl.CollisionRequest(), result)
        if count > 0 or bool(result.is_collision):
            return emit(FCLStatus.COLLISION, "fcl_contact_or_penetration")
        distance_result = fcl.DistanceResult()
        d = float(fcl.distance(
            obj_a, obj_b, fcl.DistanceRequest(), distance_result
        ))
        if not isfinite(d):
            return emit(FCLStatus.UNVERIFIED, "nonfinite_distance")
        if d > minimum_clearance_mm:
            return emit(FCLStatus.CLEAR, "fcl_distance_above_clearance", d)
        return emit(FCLStatus.UNVERIFIED, "gap_below_required_clearance", d)
    except Exception as exc:
        return emit(FCLStatus.UNVERIFIED, "fcl_query_error:" + str(exc))


def benchmark_fcl_pair(obj_a, obj_b, *, expected, iterations=1000,
                       minimum_clearance_mm=0.02):
    """Deterministic repeated-pose check, no rerendering or geometry mutation."""
    if not isinstance(expected, FCLStatus):
        raise TypeError("expected must be FCLStatus")
    if not isinstance(iterations, int) or not 1 <= iterations <= 100_000:
        raise ValueError("iterations outside [1,100000]")
    checks = [
        check_static_pair(obj_a, obj_b, minimum_clearance_mm=minimum_clearance_mm)
        for _ in range(iterations)
    ]
    times = sorted(x.elapsed_ms for x in checks)
    counts = dict(sorted(Counter(x.status.value for x in checks).items()))
    reasons = dict(sorted(Counter(x.reason for x in checks).items()))
    dists = [x.distance_mm for x in checks if x.distance_mm is not None]
    failed = sum(x.status != expected for x in checks)
    return {
        "iterations": iterations,
        "expected": expected.value,
        "pass": failed == 0,
        "failed_iterations": failed,
        "statuses": counts,
        "reasons": reasons,
        "median_ms": times[len(times)//2],
        "p95_ms": times[min(len(times)-1, int(0.95*iterations))],
        "min_distance_mm": min(dists) if dists else None,
        "max_distance_mm": max(dists) if dists else None,
        "first": checks[0].to_dict(),
        "scope": "STATIC_3D_ONLY_NOT_CONTINUOUS_OR_PRODUCTION_CERTIFIED",
    }


def build_fcl_box(size_xyz, center_xyz):
    """Build a primitive fixture using the documented python-fcl API."""
    np, fcl = _require_runtime()
    size = tuple(float(v) for v in size_xyz)
    center = tuple(float(v) for v in center_xyz)
    if (len(size) != 3 or len(center) != 3
            or not all(isfinite(v) for v in size+center)
            or not all(v > 0 for v in size)):
        raise ValueError("Invalid FCL box size or position")
    return fcl.CollisionObject(
        fcl.Box(*size), fcl.Transform(np.asarray(center, dtype=np.float64))
    )
