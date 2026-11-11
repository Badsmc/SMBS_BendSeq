"""Deterministic backgauge working-face height candidates.

Only a conservative AABB broad-phase.  Every suggested position MUST pass
actual datum-face contact and body-vs-sheet interference checks before use.
The face Z offsets are relative to the CAD datum's target machine Z, not to
the bounding-box centre of the complete backgauge.
"""
from __future__ import annotations

from math import isfinite


def contact_height_candidates(
    panel_zmin_mm: float,
    panel_zmax_mm: float,
    face_relative_zmin_mm: float,
    face_relative_zmax_mm: float,
    machine_zmin_mm: float,
    machine_zmax_mm: float,
    *,
    preferred_z_mm: float = 0.0,
) -> tuple[float, ...]:
    """Finite, inclusive range where panel and working face may overlap in Z.

    Panel [p0,p1] and translated face [z+f0,z+f1] can intersect iff
    z in [p0-f1, p1-f0].  This interval is intersected with machine travel.
    Samples favour the actual machine zero and include the interior and
    endpoints. Bounding-box overlap is never treated as physical contact.
    """
    values = (panel_zmin_mm, panel_zmax_mm,
              face_relative_zmin_mm, face_relative_zmax_mm,
              machine_zmin_mm, machine_zmax_mm, preferred_z_mm)
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not isfinite(v) for v in values):
        raise ValueError("contact height bounds must be finite numbers")
    p0, p1, f0, f1, z0, z1, wanted = map(float, values)
    if p0 > p1 or f0 >= f1 or z0 >= z1:
        raise ValueError("invalid contact height geometry or machine range")
    lo = max(z0, p0 - f1)
    hi = min(z1, p1 - f0)
    if lo > hi + 1e-7:
        return ()
    if hi < lo:
        hi = lo
    middle = (lo + hi) / 2.0

    def clamp(v: float) -> float:
        return min(hi, max(lo, v))

    # If the interval is narrow, edges may be the only feasible positions.
    # For wide intervals also probe 1 mm inside each endpoint to avoid
    # manufacturing a tangential zero-area Z-only contact at the edge.
    margin = min(1.0, (hi - lo) / 4.0)
    samples = (clamp(wanted), clamp(0.0), middle,
               lo + margin, hi - margin, lo, hi)
    out: list[float] = []
    for z in samples:
        if not any(abs(z - prev) < 1e-6 for prev in out):
            out.append(z)
    return tuple(out)


def rear_edge_at_face_height(
    polygon_sz: tuple[tuple[float, float], ...],
    face_zmin_mm: float,
    face_zmax_mm: float,
) -> float | None:
    """Largest +s coordinate of a machine-section panel inside a Z band.

    Uses the exact 2.5D section (s=-Y, z=Z) at the configured finger X
    station. It is a proposal only, never an authoritative collision or
    first-contact certificate. Returns None when no section is available.
    """
    if (not isfinite(face_zmin_mm) or not isfinite(face_zmax_mm)
            or face_zmax_mm < face_zmin_mm):
        raise ValueError("invalid face Z span")
    if len(polygon_sz) < 3:
        return None
    values = []
    for i, a in enumerate(polygon_sz):
        b = polygon_sz[(i + 1) % len(polygon_sz)]
        s0, z0 = float(a[0]), float(a[1])
        s1, z1 = float(b[0]), float(b[1])
        if not all(isfinite(x) for x in (s0, z0, s1, z1)):
            raise ValueError("nonfinite sheet section vertex")
        if face_zmin_mm <= z0 <= face_zmax_mm:
            values.append(s0)
        if abs(z1 - z0) <= 1e-12:
            continue
        for z_cut in (face_zmin_mm, face_zmax_mm):
            t = (z_cut - z0) / (z1 - z0)
            if 0.0 <= t <= 1.0:
                values.append(s0 + t * (s1 - s0))
    return max(values) if values else None
