"""Experimental OCC replay of a sheet lifted with the retracting punch.

This alternative sampled path cannot prove punch grip, continuous clearance,
or withdrawal. The stationary-sheet validator retains its own verdict.
"""
from __future__ import annotations

from math import isfinite
from time import perf_counter
from typing import Any, Callable


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
        records.append(record)
        if punch == "PENETRATION" or die_status == "PENETRATION":
            return {"status": "SAMPLED_LIFT_BLOCKED", "samples": records,
                    "reason": "native_penetration_in_assumed_synchronous_lift",
                    "continuous_certification": False}
        if punch not in ("CLEAR", "NEAR_CONTACT", "TOUCHING") or die_status not in (
                "CLEAR", "NEAR_CONTACT", "TOUCHING"):
            return {"status": "UNVERIFIED", "samples": records,
                    "reason": "invalid_or_missing_native_pair_evidence",
                    "continuous_certification": False}
    return {"status": "SAMPLED_LIFT_WITHOUT_PENETRATION_UNVERIFIED",
            "samples": records,
            "reason": "coupling_and_release_not_proven;intervals_and_withdrawal_unchecked",
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
        if (not isinstance(blocker, dict) or blocker.get("phase") != "RETRACT"
                or blocker.get("tool") != "PUNCH"):
            raise ValueError("requires_proven_retract_punch_collision")
        if not isinstance(retract, dict) or retract.get("start_z_mm") is None:
            raise ValueError("missing_retract_endpoints")
        bottom = float(retract["start_z_mm"])
        upper = float(retract["end_z_mm"])
        hit = float((blocker.get("sampled_machine_pose") or {})["ram_z_mm"])
        from .native_collision_inspector import reconstruct_exact_collision
        from .native_collision_3d import validate_workpiece_tooling_pose
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

        result = sample_synchronous_lift(
            scene["sheet"], scene["punch"], scene["die"],
            hit_ram_z_mm=hit, bottom_ram_z_mm=bottom,
            upper_ram_z_mm=upper, samples=samples, translate=translate,
            check=validate_workpiece_tooling_pose,
        )
        base.update(result)
    except Exception as exc:
        base.update(status="UNVERIFIED", samples=[],
                    reason="assisted_retract_replay_failed:{}:{}".format(
                        type(exc).__name__, str(exc)[:240]))
    base["elapsed_s"] = round(perf_counter() - started, 4)
    return base
