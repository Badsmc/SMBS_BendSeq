"""On-demand native 3D tooling check for an operator-defined bend sequence.

Runs the existing D6.3.4 trajectory validator ONE bend at a time using the
actual selected FCStd punch/die. The FoldState used for validation is a fresh
isolated instance; user playback and FCStd documents are never changed.

The backgauge is purposely visual-only, outside this collision scope.
Even a successful run proves only the sampled punch/die-vs-sheet trajectories,
not continuous clearance, transfers, clamps, frame, or manufacturing safety.
"""
from __future__ import annotations

from math import isfinite
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

from .panel_kinematics import FoldState
from .physical_validation_25d import (
    PhysicalEvidence,
    _source_path,
    check_active_25d_bend_trajectory,
)


CLEAR = PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS


def check_manual_3d_sequence(
    session: Any,
    tooling: Any,
    selected_order: Sequence[str],
    *,
    match_active_bend_length: bool = False,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    config: Any = None,
) -> dict:
    """Check a selected prefix (or complete order) with native FCStd tools.

    On a proven collision or missing evidence, stop at the affected bend;
    *never* accept an incomplete prefix as a safe full sequence. CONDITIONAL
    remains conditional and never silently becomes a PASS. A successful
    result is SAMPLED_3D_CLEAR_NOT_CONTINUOUS, not a manufacturing certificate.
    """
    t0 = perf_counter()
    order = tuple(selected_order)
    known = tuple(session.bend_order)
    state_in = session.fold_state
    if (not order or len(order) != len(set(order))
            or len(order) > len(known)
            or any(bid not in state_in.bends for bid in order)
            or order != known[:len(order)]):
        raise ValueError("manual selection must be an ordered prefix of the current bend order")

    punch = getattr(tooling, "punch", None)
    die = getattr(tooling, "die", None)
    punch_id = str(getattr(punch, "tool_id", "")) if punch is not None else ""
    die_id = str(getattr(die, "tool_id", "")) if die is not None else ""
    p_path = _source_path(punch)
    d_path = _source_path(die)
    sections = bool(
        getattr(tooling, "punch_section_offsets_xm", ())
        or getattr(tooling, "die_section_offsets_xm", ())
    )
    virtual = bool(match_active_bend_length)
    end_len = float(end_zone_length_mm)
    if not isfinite(end_len) or end_len < 0.0:
        raise ValueError("end_zone_length_mm must be finite and nonnegative")

    def final_result(status, steps, reason):
        return {
            "schema_version": "smbs.manual_native_tooling.v1",
            "status": str(status),
            "reason": str(reason),
            "requested_bends": list(order),
            "total_part_bends": len(known),
            "checked_bends": len(steps),
            "complete_part_order_requested": len(order) == len(known),
            "stopped_early": len(steps) < len(order),
            "sampled_3d_only": True,
            "continuous_motion_certified": False,
            "backgauge_collisions_checked": False,
            "transfer_clamps_frame_checked": False,
            "punch": punch_id or None,
            "die": die_id or None,
            "punch_fcstd_source": str(p_path) if p_path else None,
            "die_fcstd_source": str(d_path) if d_path else None,
            "tool_length_mode": (
                "VIRTUAL_MATCH_BEND" if virtual else "SELECTED_FCSTD_REAL_LENGTH"
            ),
            "allow_end_zone_collision": bool(allow_end_zone_collision),
            "end_zone_length_mm": end_len,
            "steps": steps,
            "elapsed_s": round(perf_counter() - t0, 4),
        }

    if p_path is None or d_path is None:
        return final_result(
            "UNVERIFIED", [],
            "selected_punch_and_die_require_verified_real_FCStd_sources",
        )
    if sections and virtual:
        return final_result(
            "UNVERIFIED", [], "native_multi_sections_incompatible_with_virtual_length",
        )

    # Fully isolated state also protects against errors/early returns in D6.
    # Do not mutate session.active_bend_index or the original FoldState.
    independent = FoldState(state_in.graph, state_in.fixed_panel)
    previous = []
    steps = []
    any_conditional = False
    check_kwargs = {
        "config": config,
        "match_active_bend_length": virtual,
        "allow_end_zone_collision": bool(allow_end_zone_collision),
        "end_zone_length_mm": end_len,
        "classify_end_zone_length_mm": end_len if end_len > 0.0 else 10.0,
    }
    for i, bend_id in enumerate(order):
        if progress_callback is not None:
            progress_callback(i + 1, len(order), bend_id)
        independent.reset_all()
        for prev in previous:
            independent.set_angle(
                prev, float(independent.bends[prev].angle_deg),
            )
        pseudo_session = SimpleNamespace(
            fold_state=independent,
            panel_build=session.panel_build,
            active_bend_id=bend_id,
            bend_order=order,
        )
        started = perf_counter()
        try:
            report = check_active_25d_bend_trajectory(
                pseudo_session, tooling, **check_kwargs,
            )
            status = report.status
            record = {
                "step_number": i + 1,
                "bend_id": bend_id,
                "angle_deg": float(independent.bends[bend_id].angle_deg),
                "completed_before_step": list(previous),
                "status": status.value,
                "reason": report.reason,
                "native_status": report.native_status,
                "checked_scope": list(report.checked_scope),
                "tool_length_info": report.tool_length_info,
                "is_conditional": bool(report.is_conditional),
            }
        except Exception as exc:
            # Errors, timeouts, OCC failures, or restoration problems are
            # missing evidence, NEVER successful clearance.
            status = PhysicalEvidence.UNVERIFIED
            record = {
                "step_number": i + 1,
                "bend_id": bend_id,
                "angle_deg": float(independent.bends[bend_id].angle_deg),
                "completed_before_step": list(previous),
                "status": "UNVERIFIED",
                "reason": "native_3d_exception:{}:{}".format(
                    type(exc).__name__, exc,
                ),
                "native_status": "EVALUATION_FAILED",
                "checked_scope": [],
                "tool_length_info": None,
                "is_conditional": False,
            }
        record["elapsed_s"] = round(perf_counter() - started, 4)
        steps.append(record)
        if status is PhysicalEvidence.COLLISION:
            return final_result("COLLISION", steps, "punch_or_die_collision_at:" + bend_id)
        if status is PhysicalEvidence.UNVERIFIED or status is PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED:
            return final_result("UNVERIFIED", steps, "native_3d_not_verified_at:" + bend_id)
        if status is PhysicalEvidence.CONDITIONAL:
            any_conditional = True
            if not allow_end_zone_collision:
                return final_result("UNVERIFIED", steps, "conditional_end_zone_not_enabled_at:" + bend_id)
        elif status is not CLEAR:
            return final_result("UNVERIFIED", steps, "unsupported_3d_status_at:" + bend_id)
        # Advance signed history only AFTER this step has been assessed;
        # the active bend was flat at the start of its own trajectory.
        previous.append(bend_id)
    if len(order) != len(known):
        return final_result(
            "UNVERIFIED", steps,
            "selected_manual_prefix_only_remaining_bends_not_checked",
        )
    if any_conditional:
        return final_result(
            "CONDITIONAL", steps,
            "punch_end_zone_interference_accepted_with_unverified_tool_relief",
        )
    return final_result(
        CLEAR.value, steps,
        "all_selected_bends_sampled_3d_clear_punch_die_only_no_continuous_certificate",
    )
