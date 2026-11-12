"""On-demand native 3D tooling check for an operator-defined bend sequence.

Runs the existing D6.3.4 trajectory validator ONE bend at a time using the
actual selected FCStd punch/die. The FoldState used for validation is a fresh
isolated instance; user playback and FCStd documents are never changed.

The backgauge is purposely visual-only, outside this collision scope.
Even a successful run proves only the sampled punch/die-vs-sheet trajectories,
not continuous clearance, transfers, clamps, frame, or manufacturing safety.
"""
from __future__ import annotations

from math import dist, isfinite
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


def retract_evidence_from_trajectory(trajectory: Any) -> dict:
    """Bounded, per-bend RETRACT evidence for the embedded FCStd JSON.

    Distinguish the *planned* ram endpoints from the physical samples that
    really ran. An unexecuted RETRACT after an earlier collision is NEVER
    reported clear. Do not embed per-frame OCC shape objects in the FCStd.
    """
    planned = getattr(trajectory, "ram_cycle_positions_mm", None) or {}
    out = {
        "status": "NOT_EVALUATED",
        "phase_evidence": "NOT_EVALUATED",
        "start_z_mm": None,
        "end_z_mm": None,
        "contact_z_mm": None,
        "bottom_z_mm": None,
        "initial_clearance_mm": None,
        "sample_count": 0,
        "sampled_start_z_mm": None,
        "sampled_end_z_mm": None,
        "end_position_sampled": False,
        "max_punch_overlap_mm3": None,
        "max_die_overlap_mm3": None,
        "reason": "RETRACT phase not reached",
    }
    for target, key in (
        ("start_z_mm", "retract_start_z_mm"),
        ("end_z_mm", "retract_end_z_mm"),
        ("contact_z_mm", "contact_z_mm"),
        ("bottom_z_mm", "bottom_z_mm"),
        ("initial_clearance_mm", "initial_clearance_mm"),
    ):
        if key in planned:
            val = float(planned[key])
            if not isfinite(val):
                raise ValueError("nonfinite planned RETRACT coordinate")
            out[target] = round(val, 6)
    for phase in getattr(trajectory, "phase_results", ()) or ():
        if str(getattr(phase, "phase_name", "")) != "RETRACT":
            continue
        raw_status = getattr(phase.evidence_status, "value",
                             str(phase.evidence_status))
        out["phase_evidence"] = str(raw_status)
        states = tuple(getattr(phase, "states", ()) or ())
        out["sample_count"] = len(states)
        if states:
            first = min(states, key=lambda st: float(st.path_u))
            last = max(states, key=lambda st: float(st.path_u))
            out["sampled_start_z_mm"] = round(float(first.ram_z_mm), 6)
            out["sampled_end_z_mm"] = round(float(last.ram_z_mm), 6)
            out["end_position_sampled"] = abs(float(last.path_u) - 1.0) < 1e-6
        out["max_punch_overlap_mm3"] = round(
            float(getattr(phase, "max_punch_overlap_volume_mm3", 0.0)), 6,
        )
        out["max_die_overlap_mm3"] = round(
            float(getattr(phase, "max_die_overlap_volume_mm3", 0.0)), 6,
        )
        diags = tuple(str(x) for x in getattr(phase, "diagnostics", ()) or ())
        out["reason"] = diags[0][:400] if diags else str(raw_status)
        any_observed_collision = any(
            getattr(getattr(st, "collision_report", None), "status", None)
            is not None
            and str(getattr(
                getattr(st.collision_report, "status"), "value",
                getattr(st.collision_report, "status"),
            )) == "PENETRATION"
            for st in states
        )
        unresolved = bool(
            getattr(phase, "unresolved_intervals", None)
            or getattr(phase, "uninspected_intervals", None)
            or getattr(phase, "discontinuities", None)
        )
        if any_observed_collision:
            out["status"] = "COLLISION"
        elif raw_status == "SAMPLED_INTERVAL_CONDITIONAL_END_ZONE":
            out["status"] = "CONDITIONAL" if not unresolved else "UNVERIFIED"
        elif (raw_status in (
            "SAMPLED_INTERVAL_CONVERGED", "ADAPTIVE_SAMPLING_CONVERGED",
        ) and out["end_position_sampled"] and not unresolved):
            out["status"] = "SAMPLED_CLEAR"
        else:
            out["status"] = "UNVERIFIED"
        # A green phase must reach the SAME planned end position as the
        # simulated FULL_CYCLE retract; otherwise fail closed.
        if out["status"] == "SAMPLED_CLEAR":
            if (out["end_z_mm"] is None
                    or abs(out["sampled_end_z_mm"] - out["end_z_mm"]) > 1e-4):
                out["status"] = "UNVERIFIED"
                out["reason"] = "RETRACT sampled end does not match configured upper Z"
        return out
    return out


def _check_selected_tool_pair_at_sampled_rams(
    punch_file: Path, die_file: Path, bend_length_mm: float,
    tooling: Any, trajectory: Any, *, virtual_length: bool,
) -> dict:
    """Test punch-vs-die solids at EVERY distinct D6 sampled ram Z.

    A tool/tool penetration is never covered by the sheet/tool end-zone
    exception. Strictly reuse selected FCStd sources and their length mode.
    This is discrete 3D evidence, not continuous collision certification.
    """
    from .native_collision_3d import CollisionClass, check_pair_clearance
    from .native_tool_loader import (
        load_native_die, load_native_punch, set_punch_ram_z,
    )
    import FreeCAD as App

    phases = getattr(trajectory, "phase_results", None) or ()
    positions = {}
    for phase in phases:
        phase_name = str(getattr(phase, "phase_name", "UNKNOWN"))
        for state in getattr(phase, "states", ()) or ():
            z = float(state.ram_z_mm)
            if not isfinite(z):
                raise ValueError("nonfinite sampled ram height")
            positions.setdefault(round(z, 6), set()).add(phase_name)
    if not positions:
        return {
            "status": "UNVERIFIED", "reason": "no_D6_ram_positions",
            "checked_ram_positions": 0,
        }

    v_len = float(bend_length_mm) if virtual_length else None
    p_sections = tuple(getattr(tooling, "punch_section_offsets_xm", ()) or ())
    d_sections = tuple(getattr(tooling, "die_section_offsets_xm", ()) or ())
    original = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    try:
        punch = load_native_punch(
            punch_file, ram_z_mm=0.0, virtual_length_mm=v_len,
            section_offsets_xm=p_sections or None,
        )
        die = load_native_die(
            die_file, virtual_length_mm=v_len,
            section_offsets_xm=d_sections or None,
        )
        for i, ram_z in enumerate(sorted(positions)):
            moved = set_punch_ram_z(punch, ram_z)
            result = check_pair_clearance(
                moved.machine_shape, die.machine_shape,
                tool_label="PUNCH_DIE",
            )
            if result.status is CollisionClass.PENETRATION:
                return {
                    "status": "COLLISION",
                    "reason": "punch_die_positive_overlap",
                    "checked_ram_positions": i + 1,
                    "ram_z_mm": ram_z,
                    "phases": sorted(positions[ram_z]),
                    "overlap_volume_mm3": float(result.overlap_volume_mm3),
                }
            if result.status not in (
                CollisionClass.CLEAR, CollisionClass.NEAR_CONTACT,
                CollisionClass.TOUCHING,
            ):
                return {
                    "status": "UNVERIFIED",
                    "reason": "punch_die_pair_unverified:" + str(result.reason),
                    "checked_ram_positions": i + 1,
                    "ram_z_mm": ram_z,
                    "phases": sorted(positions[ram_z]),
                }
        return {
            "status": "SAMPLED_CLEAR",
            "reason": "selected_FCStd_punch_die_pair_clear_at_all_D6_sampled_rams",
            "checked_ram_positions": len(positions),
        }
    finally:
        if original and App.getDocument(original) is not None:
            App.setActiveDocument(original)


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
    reverse_axis_by_bend: Optional[dict[str, bool]] = None,
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
            "punch_die_pair_checked": sum(
                item.get("punch_die_pair") is not None for item in steps
            ),
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
    reverse_axis_by_bend = dict(reverse_axis_by_bend or {})
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
        if reverse_axis_by_bend.get(bend_id, False):
            steps.append({
                "step_number": i + 1,
                "bend_id": bend_id,
                "angle_deg": float(independent.bends[bend_id].angle_deg),
                "completed_before_step": list(previous),
                "status": "UNVERIFIED",
                "reason": "reverse_axis_3d_trajectory_not_implemented",
                "native_status": "UNVERIFIED",
                "checked_scope": [],
                "tool_length_info": None,
                "retract": retract_evidence_from_trajectory(None),
                "is_conditional": False,
                "elapsed_s": 0.0,
            })
            return final_result(
                "UNVERIFIED", steps,
                "reverse_axis_3d_trajectory_not_implemented:" + bend_id,
            )
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
            retract = retract_evidence_from_trajectory(report.raw_report)
            # No sampled RETRACT -> no claim of a fully validated bend.
            # This is separate from any already proven earlier collision.
            if status in (CLEAR, PhysicalEvidence.CONDITIONAL):
                if retract["status"] not in ("SAMPLED_CLEAR", "CONDITIONAL"):
                    status = PhysicalEvidence.UNVERIFIED
            tool_pair = None
            if status in (CLEAR, PhysicalEvidence.CONDITIONAL):
                try:
                    tool_pair = _check_selected_tool_pair_at_sampled_rams(
                        p_path, d_path,
                        # True axis length, not one axis' X projection.
                        dist(*independent.bends[bend_id].axis_xyz),
                        tooling,
                        report.raw_report,
                        virtual_length=virtual,
                    )
                except Exception as pair_exc:
                    tool_pair = {
                        "status": "UNVERIFIED",
                        "reason": "punch_die_pair_exception:{}:{}".format(
                            type(pair_exc).__name__, pair_exc,
                        ),
                        "checked_ram_positions": 0,
                    }
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
                "punch_die_pair": tool_pair,
                "retract": retract,
            }
            if status is PhysicalEvidence.UNVERIFIED and report.status in (
                    CLEAR, PhysicalEvidence.CONDITIONAL):
                record["status"] = status.value
                record["reason"] = (
                    "RETRACT_not_verified:" + str(retract["reason"])
                )
            if tool_pair is not None:
                if tool_pair["status"] == "COLLISION":
                    status = PhysicalEvidence.COLLISION
                    record["status"] = status.value
                    record["reason"] = "punch_die_tool_tool_collision:" + str(
                        tool_pair.get("reason", ""),
                    )
                elif tool_pair["status"] != "SAMPLED_CLEAR":
                    status = PhysicalEvidence.UNVERIFIED
                    record["status"] = status.value
                    record["reason"] = "punch_die_tool_tool_unverified:" + str(
                        tool_pair.get("reason", ""),
                    )
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
                "punch_die_pair": None,
                "retract": retract_evidence_from_trajectory(None),
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
