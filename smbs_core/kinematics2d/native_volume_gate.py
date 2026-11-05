"""D6.3 volume consistency acceptance for reconstructed sheet states.

The current finite-radius BendZone and rigid-panel OCC model contains no
material-density, thinning or plastic-flow law. Its predicted volume is
therefore expected to remain constant across angles. Closed solid alone
does not qualify a deformed sheet for physical checking.
"""
from __future__ import annotations

from math import isfinite, radians


def volume_consistency(volumes_mm3, *, relative_tolerance=0.0005, absolute_tolerance_mm3=0.1):
    """Compare every sampled full-sheet volume with its initial flat volume.

    Return diagnostic data; caller makes the acceptance decision. Relative
    threshold 0.05% is an explicit engineering acceptance *placeholder*,
    not a claim about manufacturing material tolerances.
    """
    values = tuple(volumes_mm3)
    if (not values or not all(isfinite(v) and v > 0 for v in values)
            or not isfinite(relative_tolerance) or relative_tolerance < 0
            or not isfinite(absolute_tolerance_mm3) or absolute_tolerance_mm3 < 0):
        raise ValueError("invalid_volume_consistency_inputs")
    baseline = values[0]
    tolerance = max(absolute_tolerance_mm3, baseline * relative_tolerance)
    diffs = tuple(v - baseline for v in values)
    return {
        "reference_flat_volume_mm3": baseline,
        "sampled_volume_mm3": list(values),
        "absolute_tolerance_mm3": tolerance,
        "relative_tolerance_fraction": relative_tolerance,
        "drift_mm3": list(diffs),
        "maximum_absolute_drift_mm3": max(abs(d) for d in diffs),
        "maximum_relative_drift_percent": 100 * max(abs(d) for d in diffs) / baseline,
        "pass": all(abs(d) <= tolerance for d in diffs),
    }


def ideal_constant_thickness_bend_drift_mm3(*, thickness_mm, k_factor, bend_length_mm, angle_deg):
    """Diagnostic expected sector-area difference (not an OCC correction).

    For concentric circular inner/outer surfaces and flat neutral bend
    allowance BA=(r+K*t)*phi, cross-section area drift is approximately
    (0.5-K)*t**2*phi. Polylines introduce small discretization errors.
    """
    for v in (thickness_mm, k_factor, bend_length_mm, angle_deg):
        if not isfinite(v):
            raise ValueError("nonfinite_bend_parameter")
    if thickness_mm <= 0 or bend_length_mm <= 0 or not 0 <= k_factor <= 1:
        raise ValueError("invalid_bend_parameter")
    return (0.5-k_factor)*thickness_mm**2*radians(abs(angle_deg))*bend_length_mm
