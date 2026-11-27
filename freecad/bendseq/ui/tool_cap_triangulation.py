"""Fast, deterministic triangulation of planar tooling end profiles.

Tool caps lie on a plane of constant machine X (preview depth). The outline
may be concave: Coin3D SoFaceSet is not a reliable triangulator for such
polygons. Tessellate the actual outline before sending triangles to Coin3D.

Profiles with holes are deliberately excluded by the caller until a
hole-aware tessellator is available. Never fill a hole with fake triangles.
"""
from __future__ import annotations

from math import isfinite


class CapTriangulationError(ValueError):
    """Input profile is degenerate or cannot be safely triangulated."""


def _orient(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(p, a, b, eps):
    return (abs(_orient(a, b, p)) <= eps
            and min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def triangulate_tool_cap(vertices, *, tolerance=1e-9):
    """Return index triplets, all inside the actual concave cross section.

    Every vertex is (s, depth, z); depth must be constant to tolerance.
    The input is never modified. Duplicate and straight-line vertices are
    filtered with index preservation; input orientation is preserved.
    """
    if len(vertices) < 3:
        raise CapTriangulationError("Tool cap requires at least three points")
    points = []
    indices = []
    depth = float(vertices[0][1])
    for i, vertex in enumerate(vertices):
        s, y, z = map(float, vertex)
        if not all(isfinite(v) for v in (s, y, z)):
            raise CapTriangulationError("Nonfinite tool cap coordinate")
        if abs(y - depth) > tolerance:
            raise CapTriangulationError("Tool cap is not planar")
        p = (s, z)
        if points and (p[0] - points[-1][0]) ** 2 + (p[1] - points[-1][1]) ** 2 <= tolerance ** 2:
            continue
        points.append(p)
        indices.append(i)
    if len(points) > 1 and sum(
        (points[0][d] - points[-1][d]) ** 2 for d in (0, 1)
    ) <= tolerance ** 2:
        points.pop()
        indices.pop()

    # Remove collinear intermediate vertices; otherwise an ear can have
    # zero area, causing gaps or getting stuck on a flat profile edge.
    changed = True
    while changed and len(points) > 3:
        changed = False
        for j in range(len(points)):
            prev = points[(j - 1) % len(points)]
            mid = points[j]
            nxt = points[(j + 1) % len(points)]
            if _on_segment(mid, prev, nxt, tolerance):
                points.pop(j)
                indices.pop(j)
                changed = True
                break

    n = len(points)
    if n < 3:
        raise CapTriangulationError("Tool cap has no area")
    area2 = sum(
        points[i][0] * points[(i + 1) % n][1]
        - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    )
    if abs(area2) <= tolerance:
        raise CapTriangulationError("Tool cap area is zero")
    direction = 1.0 if area2 > 0 else -1.0
    remaining = list(range(n))
    triangles = []

    def in_or_on_triangle(p, a, b, c):
        return (direction * _orient(a, b, p) >= -tolerance
                and direction * _orient(b, c, p) >= -tolerance
                and direction * _orient(c, a, p) >= -tolerance)

    while len(remaining) > 3:
        ear = None
        for k in range(len(remaining)):
            ia, ib, ic = (remaining[(k - 1) % len(remaining)],
                          remaining[k],
                          remaining[(k + 1) % len(remaining)])
            a, b, c = points[ia], points[ib], points[ic]
            if direction * _orient(a, b, c) <= tolerance:
                continue
            if any(
                in_or_on_triangle(points[j], a, b, c)
                for j in remaining if j not in (ia, ib, ic)
            ):
                continue
            ear = (k, ia, ib, ic)
            break
        if ear is None:
            raise CapTriangulationError(
                "Tool cap is self-intersecting or too degenerate to tessellate"
            )
        k, ia, ib, ic = ear
        triangles.append((indices[ia], indices[ib], indices[ic]))
        remaining.pop(k)

    ia, ib, ic = remaining
    if direction * _orient(points[ia], points[ib], points[ic]) <= tolerance:
        raise CapTriangulationError("Tool cap has degenerate final triangle")
    triangles.append((indices[ia], indices[ib], indices[ic]))
    return tuple(triangles)
