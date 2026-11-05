"""D6.3 volume consistency acceptance for reconstructed sheet states.

Distinguishes between:
1. Physical material-volume conservation (which expects constant volume across angles).
2. CAD-consistent geometry validation (which accounts for the authoritative SheetMetal
   K-factor convention where V(theta) = V_flat + sum((0.5 - K_i) * t_i^2 * |theta_i| * L_i),
   compensated for polygonal arc chord discretization).
"""
from __future__ import annotations

from math import isfinite, radians, sin


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
    return (0.5 - k_factor) * thickness_mm**2 * radians(abs(angle_deg)) * bend_length_mm


def polygonal_bend_discretization_error_mm3(*, inner_radius_mm, outer_radius_mm,
                                            angle_deg, bend_length_mm, segments):
    """Polygonal chord discretization error for an extruded circular sector.

    The outline approximates the circular sector with `segments` chords.
    Area of n-chord sector of radius R: (n/2) * R^2 * sin(theta/n).
    Exact circular sector area: (1/2) * R^2 * theta.
    Discretization error on volume (exact - poly):
        0.5 * (Rout^2 - Rin^2) * (theta - n * sin(theta / n)) * L.
    Returns 0.0 when angle_deg is 0.
    """
    for v in (inner_radius_mm, outer_radius_mm, angle_deg, bend_length_mm):
        if not isfinite(v):
            raise ValueError("nonfinite_geometry_parameter")
    if not isinstance(segments, int) or segments < 1:
        raise ValueError("segments must be positive integer")
    if inner_radius_mm < 0 or outer_radius_mm <= inner_radius_mm or bend_length_mm <= 0:
        raise ValueError("invalid_radius_or_length")
    phi = radians(abs(angle_deg))
    if phi == 0.0:
        return 0.0
    return 0.5 * (outer_radius_mm**2 - inner_radius_mm**2) * (phi - segments * sin(phi / segments)) * bend_length_mm


def predict_cad_consistent_volume_mm3(*, baseline_flat_volume_mm3, thickness_mm, k_factor,
                                      bend_length_mm, angle_deg, inner_radius_mm=None,
                                      segments=None):
    """Calculate the expected CAD-consistent reconstructed volume for a single bend.

    If segments and inner_radius_mm are provided, compensates for the n-segment
    polygonal chord discretization introduced during extrusion.
    """
    if not isfinite(baseline_flat_volume_mm3) or baseline_flat_volume_mm3 <= 0:
        raise ValueError("invalid_baseline_flat_volume")
    drift = ideal_constant_thickness_bend_drift_mm3(
        thickness_mm=thickness_mm, k_factor=k_factor,
        bend_length_mm=bend_length_mm, angle_deg=angle_deg,
    )
    disc = 0.0
    if segments is not None:
        if inner_radius_mm is None or inner_radius_mm < 0:
            raise ValueError("inner_radius_mm required when segments specified")
        outer_radius_mm = inner_radius_mm + thickness_mm
        disc = polygonal_bend_discretization_error_mm3(
            inner_radius_mm=inner_radius_mm,
            outer_radius_mm=outer_radius_mm,
            angle_deg=angle_deg,
            bend_length_mm=bend_length_mm,
            segments=segments,
        )
    return baseline_flat_volume_mm3 + drift - disc


def cad_volume_consistency(sampled_volumes_mm3, *, angles_deg, thickness_mm, k_factor,
                           bend_length_mm, inner_radius_mm=None, segments=None,
                           relative_tolerance=0.0005, absolute_tolerance_mm3=0.1):
    """Validate single-bend reconstructed volumes against the CAD-consistent model."""
    volumes = tuple(sampled_volumes_mm3)
    angles = tuple(angles_deg)
    if len(volumes) != len(angles) or len(volumes) == 0:
        raise ValueError("sampled_volumes and angles must be non-empty tuples of equal length")
    if not all(isfinite(v) and v > 0 for v in volumes):
        raise ValueError("all sampled volumes must be positive and finite")
    if not isfinite(relative_tolerance) or relative_tolerance < 0:
        raise ValueError("relative_tolerance must be non-negative finite")
    if not isfinite(absolute_tolerance_mm3) or absolute_tolerance_mm3 < 0:
        raise ValueError("absolute_tolerance_mm3 must be non-negative finite")

    baseline_flat = volumes[0]
    predicted = []
    residuals = []
    tolerances = []

    for vol, ang in zip(volumes, angles):
        v_pred = predict_cad_consistent_volume_mm3(
            baseline_flat_volume_mm3=baseline_flat,
            thickness_mm=thickness_mm,
            k_factor=k_factor,
            bend_length_mm=bend_length_mm,
            angle_deg=ang,
            inner_radius_mm=inner_radius_mm,
            segments=segments,
        )
        predicted.append(v_pred)
        res = vol - v_pred
        residuals.append(res)
        tol = max(absolute_tolerance_mm3, v_pred * relative_tolerance)
        tolerances.append(tol)

    max_abs_res = max(abs(r) for r in residuals)
    max_rel_pct = 100.0 * max(abs(r) / p for r, p in zip(residuals, predicted))
    all_pass = all(abs(r) <= tol for r, tol in zip(residuals, tolerances))

    return {
        "pass": all_pass,
        "status": "PASS" if all_pass else "FAIL",
        "reference_model": "SHEETMETAL_CONSTANT_THICKNESS_CONCENTRIC_BEND",
        "baseline_flat_volume_mm3": baseline_flat,
        "sampled_volume_mm3": list(volumes),
        "predicted_volume_mm3": predicted,
        "residuals_mm3": residuals,
        "maximum_absolute_residual_mm3": max_abs_res,
        "maximum_relative_residual_percent": max_rel_pct,
        "tolerances_mm3": tolerances,
        "relative_tolerance_fraction": relative_tolerance,
        "absolute_tolerance_mm3": absolute_tolerance_mm3,
    }


def multi_bend_cad_volume_consistency(sampled_volumes_mm3, *, state_angles_list,
                                      bends_params, segments=None,
                                      relative_tolerance=0.0005, absolute_tolerance_mm3=0.1):
    """Validate multi-bend reconstructed volumes across intermediate states.

    state_angles_list: list of dict[bend_id, angle_deg] for each sampled state.
    bends_params: dict[bend_id, dict] with keys:
        - thickness_mm
        - k_factor
        - bend_length_mm
        - inner_radius_mm
    """
    volumes = tuple(sampled_volumes_mm3)
    states = tuple(state_angles_list)
    if len(volumes) != len(states) or len(volumes) == 0:
        raise ValueError("volumes and state_angles_list must be equal non-empty length")
    if not all(isfinite(v) and v > 0 for v in volumes):
        raise ValueError("all sampled volumes must be positive and finite")

    baseline_flat = volumes[0]
    predicted = []
    residuals = []
    tolerances = []

    for vol, angles_map in zip(volumes, states):
        v_pred = baseline_flat
        for bid, ang in angles_map.items():
            bp = bends_params[bid]
            t = bp["thickness_mm"]
            k = bp["k_factor"]
            L = bp["bend_length_mm"]
            r_in = bp.get("inner_radius_mm")
            drift = ideal_constant_thickness_bend_drift_mm3(
                thickness_mm=t, k_factor=k, bend_length_mm=L, angle_deg=ang
            )
            disc = 0.0
            if segments is not None and r_in is not None:
                disc = polygonal_bend_discretization_error_mm3(
                    inner_radius_mm=r_in,
                    outer_radius_mm=r_in + t,
                    angle_deg=ang,
                    bend_length_mm=L,
                    segments=segments,
                )
            v_pred += (drift - disc)

        predicted.append(v_pred)
        res = vol - v_pred
        residuals.append(res)
        tol = max(absolute_tolerance_mm3, v_pred * relative_tolerance)
        tolerances.append(tol)

    max_abs_res = max(abs(r) for r in residuals)
    max_rel_pct = 100.0 * max(abs(r) / p for r, p in zip(residuals, predicted))
    all_pass = all(abs(r) <= tol for r, tol in zip(residuals, tolerances))

    return {
        "pass": all_pass,
        "status": "PASS" if all_pass else "FAIL",
        "reference_model": "SHEETMETAL_MULTI_BEND_SUM_CONCENTRIC_MODEL",
        "baseline_flat_volume_mm3": baseline_flat,
        "sampled_volume_mm3": list(volumes),
        "predicted_volume_mm3": predicted,
        "residuals_mm3": residuals,
        "maximum_absolute_residual_mm3": max_abs_res,
        "maximum_relative_residual_percent": max_rel_pct,
        "tolerances_mm3": tolerances,
        "relative_tolerance_fraction": relative_tolerance,
        "absolute_tolerance_mm3": absolute_tolerance_mm3,
    }
