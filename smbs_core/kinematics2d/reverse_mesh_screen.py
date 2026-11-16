"""Pure-Python triangle-prism broad/narrow screen for reverse search ranking.

FreeCAD face triangulation is created once per search. Each flat triangle is
extruded to sheet thickness and rigidly placed using existing FoldState.
Convex separating axis tests rank potential panel/panel interference without
BRep creation. It is NOT a physical validation engine: curved bend regions,
manufacturing tolerances and machine tools still require independent D6.

An overlapping prism is approximate evidence, never justification to prune.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any


Vec = tuple[float, float, float]


@dataclass(frozen=True)
class MeshScreenStats:
    panel_meshes: int
    triangles: int
    missing_panels: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "panel_meshes": self.panel_meshes,
            "triangles": self.triangles,
            "missing_panels": list(self.missing_panels),
            "method": "TESSSELLATED_FACE_TRIANGULAR_PRISMS_SAT_RANKING_ONLY",
        }


def _coordinates(vertex: Any) -> Vec:
    point = getattr(vertex, "Point", vertex)
    if hasattr(point, "x"):
        return (float(point.x), float(point.y), float(getattr(point, "z", 0.0)))
    return (float(point[0]), float(point[1]), float(point[2]))


def extract_panel_triangles(panel_build: Any, *, deviation_mm: float = 1.0,
                            max_triangles_per_panel: int = 5000) -> dict:
    """Read OCC face tessellation once. Missing tessellation disables mesh
    refinement for that panel; it NEVER assumes the panel is empty.
    """
    meshes = {}
    for panel, face in zip(
        getattr(panel_build, "panels", ()) or (),
        getattr(panel_build, "panel_faces", ()) or (),
    ):
        pid = str(getattr(panel, "panel_id", ""))
        if not pid or not hasattr(face, "tessellate"):
            continue
        try:
            vertices, indices = face.tessellate(float(deviation_mm))
            vertices = tuple(_coordinates(vertex) for vertex in vertices)
            if not 0 < len(indices) <= max_triangles_per_panel:
                continue
            tris = []
            for ids in indices:
                if len(ids) != 3:
                    raise ValueError("nontriangular tessellation")
                triangle = tuple(vertices[int(i)] for i in ids)
                if not all(isfinite(x) for p in triangle for x in p):
                    raise ValueError("nonfinite triangle coordinate")
                tris.append(triangle)
            meshes[pid] = tuple(tris)
        except (ValueError, TypeError, IndexError, AttributeError, RuntimeError):
            continue
    return meshes


def _dot(a: Vec, b: Vec) -> float:
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1]*b[2]-a[2]*b[1],
            a[2]*b[0]-a[0]*b[2],
            a[0]*b[1]-a[1]*b[0])


def _bounds(points):
    return (tuple(min(p[k] for p in points) for k in range(3)) +
            tuple(max(p[k] for p in points) for k in range(3)))


def _aabb_overlap(a, b, tol=1e-6):
    return all(min(a[k+3], b[k+3]) - max(a[k], b[k]) > tol
               for k in range(3))


def _prism_edges(points):
    # Six vertices: upper triangle 0,1,2; lower triangle 3,4,5.
    return (_sub(points[1], points[0]),
            _sub(points[2], points[1]),
            _sub(points[0], points[2]),
            _sub(points[3], points[0]))


def _prism_normals(edges):
    a, b, c, depth = edges
    return (_cross(a, b),
            _cross(a, depth),
            _cross(b, depth),
            _cross(c, depth))


def prisms_intersect(a, b, *, tolerance_mm=1e-5):
    """SAT on two triangular prisms; true means approximate overlap.

    A degenerate or uncertain axis is deliberately not counted as a
    separating axis. Equality/contact is not treated as volume penetration.
    """
    if not _aabb_overlap(_bounds(a), _bounds(b), tolerance_mm):
        return False
    e1, e2 = _prism_edges(a), _prism_edges(b)
    axes = list(_prism_normals(e1)) + list(_prism_normals(e2))
    axes.extend(_cross(x, y) for x in e1 for y in e2)
    tested = 0
    for axis in axes:
        length2 = _dot(axis, axis)
        if length2 < 1e-20:
            continue
        tested += 1
        lo1 = min(_dot(p, axis) for p in a)
        hi1 = max(_dot(p, axis) for p in a)
        lo2 = min(_dot(p, axis) for p in b)
        hi2 = max(_dot(p, axis) for p in b)
        if min(hi1, hi2) - max(lo1, lo2) <= tolerance_mm * sqrt(length2):
            return False
    return tested > 0


def transformed_prisms(mesh, transform, thickness):
    """Transform local face triangles without changing source OCC solids."""
    depth = transform.point((0.0, 0.0, -thickness))
    origin = transform.point((0.0, 0.0, 0.0))
    offset = _sub(depth, origin)
    result = []
    for triangle in mesh:
        top = tuple(transform.point(v) for v in triangle)
        bottom = tuple((p[0]+offset[0], p[1]+offset[1], p[2]+offset[2])
                       for p in top)
        points = top + bottom
        result.append((_bounds(points), points))
    return tuple(result)


def approximate_overlap_count(prisms_a, prisms_b, *, limit=40,
                              deadline=None, clock=None):
    """Count probable triangle-prism intersections; capped for bounded work.

    The output is a ranking penalty, NOT a collision/no-collision verdict.
    """
    count = 0
    for box_a, tri_a in prisms_a:
        for box_b, tri_b in prisms_b:
            if deadline is not None and clock is not None and clock() >= deadline:
                return count
            if not _aabb_overlap(box_a, box_b):
                continue
            if prisms_intersect(tri_a, tri_b):
                count += 1
                if count >= limit:
                    return count
    return count
