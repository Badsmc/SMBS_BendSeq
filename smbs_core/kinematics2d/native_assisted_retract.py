"""Experimental OCC replay of a sheet lifted with the retracting punch.

This alternative sampled path cannot prove punch grip, continuous clearance,
or withdrawal. The stationary-sheet validator retains its own verdict.
"""
from __future__ import annotations

from math import isfinite
from time import perf_counter
from typing import Any, Callable


def eligible_assisted_retract(blocker: Any, retract: Any) -> bool:
    """Only expose the probe for a reproducible punch hit on RETRACT."""
    if (not isinstance(blocker, dict) or not isinstance(retract, dict)
            or blocker.get("phase") != "RETRACT" or blocker.get("tool") != "PUNCH"):
        return False
    pose = blocker.get("sampled_machine_pose") or {}
    try:
        hit = float(pose["ram_z_mm"])
        bottom = float(retract["start_z_mm"])
        upper = float(retract["end_z_mm"])
    except (KeyError, TypeError, ValueError):
        return False
    return (all(isfinite(v) for v in (hit, bottom, upper))
            and bottom <= hit <= upper and bottom < upper)


def historic_lift_candidate(
    report: Any, *, order: Any, punch_id: str, die_id: str,
    virtual_length: bool, end_zone_allow: bool, end_zone_mm: float,
) -> dict | None:
    """Offer an old FCStd blocker only for an identically selected setup.

    A matching historical record is *not* native evidence. The on-demand
    inspector must still reconstruct exact source geometry and reproduce its
    recorded Common volume before any hypothetical lift can be evaluated.
    """
    if not isinstance(report, dict) or report.get("status") != "COLLISION":
        return None
    if (tuple(report.get("requested_bends") or ()) != tuple(order)
            or str(report.get("punch")) != str(punch_id)
            or str(report.get("die")) != str(die_id)
            or bool(report.get("allow_end_zone_collision")) != bool(end_zone_allow)):
        return None
    expected_length_mode = ("VIRTUAL_MATCH_BEND" if virtual_length
                            else "SELECTED_FCSTD_REAL_LENGTH")
    try:
        if (report.get("tool_length_mode") != expected_length_mode
                or abs(float(report["end_zone_length_mm"]) - float(end_zone_mm)) > 1e-6):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    for step in report.get("steps") or ():
        if not isinstance(step, dict) or step.get("status") != "COLLISION":
            continue
        blocker, retract = step.get("first_blocker"), step.get("retract")
        if eligible_assisted_retract(blocker, retract):
            return step
    return None


def sample_synchronous_lift(
    sheet: Any, punch_at_hit: Any, die: Any, *,
    hit_ram_z_mm: float, bottom_ram_z_mm: float, upper_ram_z_mm: float,
    samples: int = 9, max_time_s: float = 120.0,
    translate: Callable[[Any, float], Any],
    check: Callable[[Any, Any, Any], Any],
) -> dict:
    """Sample a one-to-one sheet/punch lift from BOTTOM; never return CLEAR."""
    values = (hit_ram_z_mm, bottom_ram_z_mm, upper_ram_z_mm)
    if (not all(isfinite(float(v)) for v in values)
            or not bottom_ram_z_mm <= hit_ram_z_mm <= upper_ram_z_mm
            or upper_ram_z_mm <= bottom_ram_z_mm
            or not isfinite(float(max_time_s)) or max_time_s <= 0
            or not isinstance(samples, int) or not 2 <= samples <= 65):
        raise ValueError("invalid_assisted_retract_range_or_sample_count")
    if any(shape is None for shape in (sheet, punch_at_hit, die)):
        raise ValueError("missing_native_assisted_retract_shape")

    records = []
    conditional_count = 0
    started = perf_counter()
    for index in range(samples):
        if index and perf_counter() - started >= max_time_s:
            return {"status": "UNVERIFIED", "samples": records,
                    "reason": "assisted_lift_time_budget_exhausted",
                    "continuous_certification": False}
        fraction = index / (samples - 1)
        dz = fraction * (upper_ram_z_mm - bottom_ram_z_mm)
        ram_z = bottom_ram_z_mm + dz
        # The adapter must copy: source FCStd shapes are never moved in place.
        moved_sheet = translate(sheet, dz)
        moved_punch = translate(punch_at_hit, ram_z - hit_ram_z_mm)
        result = check(moved_sheet, moved_punch, die)
        punch = str(getattr(getattr(result.punch_result, "status", None), "value",
                            getattr(result.punch_result, "status", "")))
        die_status = str(getattr(getattr(result.die_result, "status", None), "value",
                                 getattr(result.die_result, "status", "")))
        record = {
            "u": round(fraction, 6), "ram_z_mm": ram_z,
            "sheet_lift_mm": dz, "punch": punch, "die": die_status,
            "punch_overlap_mm3": float(result.punch_result.overlap_volume_mm3),
            "die_overlap_mm3": float(result.die_result.overlap_volume_mm3),
        }
        zone = getattr(result, "punch_end_zone_analysis", None)
        zone_kind = getattr(getattr(zone, "classification", None), "value", None)
        conditional = (punch == "PENETRATION"
                       and die_status in ("CLEAR", "NEAR_CONTACT", "TOUCHING")
                       and getattr(result, "is_conditional_safe", False) is True
                       and getattr(result, "conditional_status", None) == "CONDITIONAL"
                       and zone_kind == "END_ZONE_ONLY")
        record["conditional_end_zone"] = conditional
        if conditional:
            conditional_count += 1
            record["central_overlap_mm3"] = float(
                zone.central_zone_overlap_volume_mm3
            )
        records.append(record)
        if (punch == "PENETRATION" and not conditional) or die_status == "PENETRATION":
            return {"status": "SAMPLED_LIFT_BLOCKED", "samples": records,
                    "reason": "native_penetration_in_assumed_synchronous_lift",
                    "continuous_certification": False}
        if (punch not in ("CLEAR", "NEAR_CONTACT", "TOUCHING") and not conditional) or die_status not in (
                "CLEAR", "NEAR_CONTACT", "TOUCHING"):
            return {"status": "UNVERIFIED", "samples": records,
                    "reason": "invalid_or_missing_native_pair_evidence",
                    "continuous_certification": False}
    return {"status": ("SAMPLED_LIFT_CONDITIONAL_UNVERIFIED" if conditional_count
                       else "SAMPLED_LIFT_WITHOUT_PENETRATION_UNVERIFIED"),
            "samples": records,
            "reason": ("end_zone_overlap_requires_relief_punch;" if conditional_count else "")
                      + "coupling_and_release_not_proven;intervals_and_withdrawal_unchecked",
            "conditional_samples": conditional_count,
            "continuous_certification": False}


def inspect_punch_assisted_retract(
    session: Any, tooling: Any, bend_id: str, blocker: dict, retract: dict,
    *, samples: int = 9,
) -> dict:
    """Replay native FCStd tools on a hypothetical synchronous lift.

    The existing exact collision inspector first verifies the hit. An error
    returns UNVERIFIED; this cannot change the Manual sequence verdict.
    """
    started = perf_counter()
    base = {"schema_version": "smbs.assisted_retract_experiment.v1",
            "bend_id": str(bend_id), "existing_verdict": "COLLISION",
            "assumption": "sheet_follows_punch_1_to_1_from_BOTTOM",
            "withdrawal_checked": False, "coupling_proven": False,
            "continuous_certification": False}
    try:
        if not eligible_assisted_retract(blocker, retract):
            raise ValueError("requires_proven_retract_punch_collision")
        bottom = float(retract["start_z_mm"])
        upper = float(retract["end_z_mm"])
        hit = float((blocker.get("sampled_machine_pose") or {})["ram_z_mm"])
        from .native_collision_inspector import reconstruct_exact_collision
        from .native_collision_3d import (
            validate_workpiece_tooling_pose, EndZoneCollisionPolicy,
        )
        import FreeCAD as App

        scene = reconstruct_exact_collision(
            session, tooling, bend_id, blocker, include_components=False,
        )
        if abs(float(scene["pose"]["ram_z_mm"]) - hit) > 1e-6:
            raise ValueError("replayed_ram_mismatch")
        base["baseline_hit_overlap_mm3"] = float(scene["volumes_mm3"]["PUNCH"])
        base["bottom_ram_z_mm"] = bottom
        base["upper_ram_z_mm"] = upper

        def translate(shape, dz):
            moved = shape.copy()
            moved.translate(App.Vector(0, 0, dz))
            return moved

        allow_end_zone = bool(getattr(tooling, "allow_end_zone_collision", False))
        end_zone_mm = float(getattr(tooling, "end_zone_length_mm", 0.0))
        if not isfinite(end_zone_mm) or end_zone_mm < 0.0:
            raise ValueError("invalid_selected_end_zone_policy")
        policy = EndZoneCollisionPolicy(allow_end_zone, end_zone_mm)

        def check(sheet, punch, die):
            return validate_workpiece_tooling_pose(
                sheet, punch, die, end_zone_policy=policy,
                classify_end_zone_length_mm=(end_zone_mm if end_zone_mm > 0 else 10.0),
            )

        base["selected_end_zone_policy"] = {
            "allow": allow_end_zone, "length_mm": end_zone_mm,
        }

        result = sample_synchronous_lift(
            scene["sheet"], scene["punch"], scene["die"],
            hit_ram_z_mm=hit, bottom_ram_z_mm=bottom,
            upper_ram_z_mm=upper, samples=samples, translate=translate,
            check=check,
        )
        base.update(result)
    except Exception as exc:
        base.update(status="UNVERIFIED", samples=[],
                    reason="assisted_retract_replay_failed:{}:{}".format(
                        type(exc).__name__, str(exc)[:240]))
    base["elapsed_s"] = round(perf_counter() - started, 4)
    return base
