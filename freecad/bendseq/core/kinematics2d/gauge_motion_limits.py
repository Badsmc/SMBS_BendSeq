"""Conservative CAD-envelope limits for the visual rear stop.

Coordinates: (S, X, Z), relative to the centre of the gauge contact face.
This controls the preview, not the search or a collision certification.
"""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class GaugeHeight:
    z_mm: float | None
    limited: bool
    contact_possible: bool
    overlaps_die: bool


def bounded_gauge_height(requested_z, preferred_z, z_min, z_max, *,
                         s_mm, stations, gauge_bounds, face_z_span,
                         die_polygon=(), die_spans=(), clearance_mm=0.0):
    """Retain previous Z if the working face still covers the desired edge.

    Overlap in both horizontal axes forces the complete gauge body above
    the die top. A missing die X span is treated conservatively as infinite.
    No feasible range returns None so the caller can hide the gauge visibly.
    """
    values = (requested_z, preferred_z, z_min, z_max, s_mm, clearance_mm,
              *gauge_bounds, *face_z_span, *stations,
              *(v for point in die_polygon for v in point),
              *(v for span in die_spans for v in span))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in values):
        raise ValueError("nonfinite gauge geometry or travel limits")
    if z_min > z_max or clearance_mm < 0:
        raise ValueError("invalid gauge travel limits")
    s0, s1, x0, x1, bottom, top = gauge_bounds
    f0, f1 = face_z_span
    if s0 > s1 or x0 > x1 or bottom > top or f0 > f1:
        raise ValueError("invalid gauge CAD envelope")
    overlaps = False
    lo, hi = z_min, z_max
    if die_polygon and stations:
        ds0, ds1 = min(s for s, z in die_polygon), max(s for s, z in die_polygon)
        overlaps_s = s_mm + s1 >= ds0 and s_mm + s0 <= ds1
        overlaps_x = (not die_spans or any(
            x + x1 >= a and x + x0 <= b for x in stations for a, b in die_spans))
        overlaps = overlaps_s and overlaps_x
        if overlaps:
            lo = max(lo, max(z for s, z in die_polygon) - bottom + clearance_mm)
    if lo > hi:
        return GaugeHeight(None, True, False, overlaps)
    contact_lo = max(lo, requested_z - f1)
    contact_hi = min(hi, requested_z - f0)
    possible = contact_lo <= contact_hi
    z = (min(contact_hi, max(contact_lo, preferred_z)) if possible
         else min(hi, max(lo, requested_z)))
    return GaugeHeight(z, abs(z - requested_z) > 1e-7, possible, overlaps)


def flange_below_opening(flange_mm, opening_mm):
    """User-requested full-V nominal flange warning; not a process solver."""
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not isfinite(v) or v <= 0 for v in (flange_mm, opening_mm)):
        return False
    return flange_mm < opening_mm - 1e-7


def gauge_transfer(start, end, start_stations, end_stations, progress, z_min, z_max,
                   *, gauge_bounds, face_z_span, die_polygon=(), die_spans=()):
    """Raise, traverse, then lower; check the swept horizontal CAD envelope."""
    if len(start_stations) != len(end_stations):
        raise ValueError("gauge station count changed during transfer")
    t = max(0.0, min(1.0, float(progress)))
    a_s, a_z = start
    b_s, b_z = end
    s0, s1, x0, x1, z0, z1 = gauge_bounds
    ds = b_s - a_s
    swept_bounds = (s0 + min(0.0, ds), s1 + max(0.0, ds), x0, x1, z0, z1)
    # Include the entire X travel, not only the endpoint stations.
    min_x = min((*start_stations, *end_stations), default=0.0)
    max_x = max((*start_stations, *end_stations), default=0.0)
    swept_bounds = (*swept_bounds[:2], x0, x1 + max_x - min_x, z0, z1)
    safe = bounded_gauge_height(max(a_z, b_z), max(a_z, b_z), z_min, z_max,
                               s_mm=a_s, stations=(min_x,), gauge_bounds=swept_bounds,
                               face_z_span=(0.0, 0.0), die_polygon=die_polygon,
                               die_spans=die_spans)
    if safe.z_mm is None:
        return None
    height = max(a_z, b_z, safe.z_mm)
    if height > z_max or min(a_z, b_z) < z_min:
        return None
    if t == 0.0:
        return a_s, a_z, tuple(start_stations)
    if t == 1.0:
        return b_s, b_z, tuple(end_stations)
    if t < 0.2:
        return a_s, a_z + (height - a_z) * t / 0.2, tuple(start_stations)
    if t <= 0.8:
        u = (t - 0.2) / 0.6
        return a_s + ds * u, height, tuple(a + (b - a) * u for a, b in zip(start_stations, end_stations))
    return b_s, height + (b_z - height) * (t - 0.8) / 0.2, tuple(end_stations)
