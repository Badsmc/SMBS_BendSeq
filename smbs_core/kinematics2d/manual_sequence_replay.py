"""Sequential sampled-2D diagnostic replay of an operator-defined bend order.

Uses the same Stage A / Stage B screens as automated planar search. This is
diagnostic evidence only: it does not approve physical 3D manufacturability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Optional, Sequence

from dataclasses import replace
from .operation_cost import OperationWeights, compute_workpiece_machine_setup, score_sequence
from .backgauge_2d import BackgaugeModel2D

from .preliminary_screening import (
    ScreeningDecision, screen_candidate_stage_a, screen_candidate_stage_b,
)
from .sequence_search_2d import _classify_step, _setup_with_scored_tool_lengths


def crosscheck_manual_planar_native_evidence(planar: dict, physical: dict) -> list[dict]:
    """Identify per-bend 2D/native contradictions without certifying safety.

    A sampled 3D clearance is not a continuous certificate. 2D section
    interference likewise does not prove native full-span tool penetration.
    Preserve both independent results in developer diagnostics.
    """
    planar_steps = {
        str(s.get("bend_id")): s
        for s in planar.get("steps", [])
        if s.get("bend_id") is not None
    }
    discrepancies = []
    for native in physical.get("steps", []):
        bid = str(native.get("bend_id"))
        planar_step = planar_steps.get(bid)
        if planar_step is None:
            continue
        pstatus = str(planar_step.get("evidence", ""))
        nstatus = str(native.get("status", ""))
        if (pstatus == "2D_INTERFERENCE"
                and nstatus == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"):
            kind = "2D_SECTION_INTERFERENCE_NOT_REPRODUCED_IN_SAMPLED_NATIVE_3D"
        elif (pstatus == "SAMPLED_2D_CLEAR_OR_TOUCH"
                and nstatus == "COLLISION"):
            kind = "NATIVE_COLLISION_NOT_DETECTED_BY_SAMPLED_2D"
        else:
            continue
        discrepancies.append({
            "bend_id": bid,
            "kind": kind,
            "planar_evidence": pstatus,
            "planar_reason": planar_step.get("reason"),
            "native_evidence": nstatus,
            "native_reason": native.get("reason"),
            "reverse_axis_2d": bool(planar_step.get("reverse_axis", False)),
            "reverse_axis_3d": bool(native.get("reverse_axis", False)),
            "continuous_clearance_certified": False,
            "physical_verdict_unchanged": True,
        })
    return discrepancies


def off_section_prior_fold_risk(fold_state: Any, completed_bends: Sequence[str]) -> dict:
    """Name previously folded panels invisible to center-plane 2D screening.

    This is an honest cheap *risk indicator*, not a claim of intersection.
    The final selected native 3D punch/die solids decide collision.
    """
    moved = sorted({
        pid
        for bend_id in completed_bends
        for pid in getattr(fold_state.bends[bend_id], "moving_subtree", ())
    })
    return {
        "scope": "2D_ACTIVE_AXIS_CROSS_SECTION_ONLY",
        "native_full_span_3d_required": bool(completed_bends),
        "completed_bends": list(completed_bends),
        "previously_folded_panels": moved,
        "status": (
            "UNVERIFIED_OUT_OF_SECTION_PANELS"
            if completed_bends else "NO_PRIOR_FOLDS"
        ),
        "reason": (
            "A center-plane 2D pass cannot exclude a prior folded flange "
            "striking the real punch at another position along its length."
            if completed_bends else
            "No previously completed bends; 3D tooling remains unverified."
        ),
    }


def evaluate_manual_planar_order(
    session: Any,
    tooling: Any,
    selected_order: Sequence[str],
    *,
    match_active_bend_length: bool = True,
    tolerance_mm: float = 0.02,
    allow_retract_warning: bool = False,
    weights: Optional[OperationWeights] = None,
    backgauge_model: Optional[BackgaugeModel2D] = None,
    reverse_axis_by_bend: Optional[dict[str, bool]] = None,
) -> dict:
    """Replay the explicit sequence, stopping at the first non-clear Stage A/B.

    A subset is permitted; remaining bends are left flat. All FoldState
    angles/transforms are restored in finally, even on geometry failures.
    Results are intentionally NOT 3D safety certification.
    """
    start = perf_counter()
    state = session.fold_state
    known = tuple(session.canonical_order)
    sequence = tuple(selected_order)
    if (len(set(sequence)) != len(sequence)
            or any(bid not in state.bends for bid in sequence)
            or len(sequence) > len(known)):
        raise ValueError("manual sequence contains unknown or duplicate bend IDs")
    if not sequence:
        raise ValueError("manual sequence is empty")
    originals = (dict(state.angles), dict(state.transforms))
    steps = []
    accepted = []
    stop_reason = None
    op_weights = weights if weights is not None else OperationWeights()
    if (backgauge_model is not None and backgauge_model.part_label
            and backgauge_model.part_label != getattr(session, "part_label", "")):
        raise ValueError("Backgauge targets belong to another part: " +
                         backgauge_model.part_label)
    accepted_setups = []
    reverse_axis_by_bend = dict(reverse_axis_by_bend or {})
    cad_checker = None
    if backgauge_model is not None and backgauge_model.cad_geometry_enabled:
        from .backgauge_cad_3d import BackgaugeCAD3D
        cad_checker = BackgaugeCAD3D(
            station_xm_mm=backgauge_model.cad_station_xm_mm,
                second_station_xm_mm=backgauge_model.cad_second_station_xm_mm,
        )
    try:
        for bid in sequence:
            step_start = perf_counter()
            reverse_axis = bool(reverse_axis_by_bend.get(bid, False))
            state.reset_all()
            for previous in accepted:
                state.set_angle(previous, float(state.bends[previous].angle_deg))
            record = {
                "step_number": len(accepted) + 1,
                "bend_id": bid,
                "angle_deg": float(state.bends[bid].angle_deg),
                "reverse_axis": reverse_axis,
                "completed_before_step": list(accepted),
                "off_section_3d_risk": off_section_prior_fold_risk(
                    state, accepted,
                ),
            }
            try:
                setup = _setup_with_scored_tool_lengths(
                    compute_workpiece_machine_setup(
                        state, bid,
                        reverse_axis=reverse_axis,
                        rear_panel_vertices_xy=(
                            session.panel_vertices_xy
                            if backgauge_model is not None else None
                        ),
                    ),
                    tooling, match_active_bend_length,
                )
                gauge_polygon = None
                cad_contact_s = cad_contact_z = None
                cad_contact_data = None
                if backgauge_model is not None:
                    if cad_checker is not None:
                        point, depth, source, cad_contact_data = (
                            cad_checker.resolve_contact_for_bend(
                                state, session.panel_vertices_xy, bid,
                                backgauge_model, tolerance_mm=tolerance_mm,
                            )
                        )
                        cad_contact_s = cad_contact_data["contact_s_mm"]
                        cad_contact_z = cad_contact_data["contact_z_mm"]
                    else:
                        point, depth, source = backgauge_model.resolve_for_state(
                            state, session.panel_vertices_xy, bid,
                        )
                        gauge_polygon = backgauge_model.polygon_at(point)
                    setup = replace(
                        setup, backgauge_s_mm=point.s_mm,
                        backgauge_z_mm=point.z_mm,
                        note=(setup.note + " | " if setup.note else "") +
                        source + (":depth_mm={:.3f}".format(depth)
                                  if depth == depth else ""),
                    )
                record["machine_setup"] = setup.to_dict()
                stage_a = screen_candidate_stage_a(
                    state, bid, tooling,
                    match_active_bend_length=match_active_bend_length,
                )
                record["stage_a"] = stage_a.to_dict()
                if stage_a.decision is ScreeningDecision.PROVEN_BLOCKING:
                    verdict = ("INVALID_INPUT" if stage_a.stage in
                               ("STAGE_A_INPUT", "STAGE_A_GEOMETRY")
                               else "2D_TOOLING_CONSTRAINT")
                    record["stage_b"] = None
                    reason = stage_a.reason
                else:
                    stage_b = screen_candidate_stage_b(
                        state, bid, tooling, session.panel_vertices_xy,
                        match_active_bend_length=match_active_bend_length,
                        fractions=tuple(i / 16 for i in range(17)),
                        stroke_fractions=tuple(i / 8 for i in range(9)),
                        tolerance_mm=tolerance_mm,
                        allow_retract_warning=allow_retract_warning,
                        reverse_axis=reverse_axis,
                        backgauge_polygon_machine=gauge_polygon,
                        backgauge_cad_checker=cad_checker,
                        backgauge_contact_s_mm=cad_contact_s,
                        backgauge_contact_z_mm=cad_contact_z,
                        backgauge_contact_metadata=cad_contact_data,
                        backgauge_initial_feed_mm=(
                            backgauge_model.initial_feed_preview_mm
                            if cad_checker is not None and not accepted else 0.0
                        ),
                    )
                    record["stage_b"] = stage_b.to_dict()
                    verdict = _classify_step(stage_b)
                    reason = stage_b.reason
                record["evidence"] = verdict
                record["reason"] = reason
                record["conditional_retract_warning"] = (
                    verdict == "2D_RETRACT_WARNING_NOT_3D_VALIDATED"
                )
                record["elapsed_s"] = round(perf_counter() - step_start, 6)
                steps.append(record)
                if verdict != "SAMPLED_2D_CLEAR_OR_TOUCH" and not (
                        allow_retract_warning
                        and verdict == "2D_RETRACT_WARNING_NOT_3D_VALIDATED"):
                    stop_reason = {
                        "step_number": record["step_number"],
                        "bend_id": bid, "evidence": verdict, "reason": reason,
                    }
                    break
                accepted.append(bid)
                accepted_setups.append(setup)
                score = score_sequence(accepted, accepted_setups, op_weights)
                record["operation_penalty"] = score.steps[-1].to_dict()
                record["cumulative_penalty"] = round(float(score.total), 4)
            except Exception as exc:
                from .backgauge_cad_3d import BackgaugeCADError
                physical_obstruction = (
                    isinstance(exc, BackgaugeCADError)
                    and str(exc).startswith(
                        "OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT:"
                    )
                )
                configured_stop_unreachable = (
                    isinstance(exc, BackgaugeCADError)
                    and str(exc).startswith(
                        "NO_ACCESSIBLE_REAR_EDGE_AT_CONFIGURED_X:"
                    )
                )
                invalid_stop_configuration = (
                    isinstance(exc, BackgaugeCADError)
                    and str(exc).startswith((
                        "TWO_CAD_FINGERS_REQUIRE_INDEPENDENT_REAR_EDGE_SOLVER",
                        "AUTO_CAD_CONTACT_REQUIRES_ZERO_EDGE_CLEARANCE:",
                    ))
                )
                record["evidence"] = (
                    "CAD_3D_INTERFERENCE" if physical_obstruction
                    else "CAD_CONTACT_UNAVAILABLE"
                    if configured_stop_unreachable
                    else "INVALID_INPUT" if invalid_stop_configuration
                    else "UNVERIFIED_2D"
                )
                record["reason"] = "replay_exception:{}:{}".format(
                    type(exc).__name__, exc)
                record["elapsed_s"] = round(perf_counter() - step_start, 6)
                steps.append(record)
                stop_reason = {
                    "step_number": record["step_number"],
                    "bend_id": bid,
                    "evidence": record["evidence"],
                    "reason": record["reason"],
                }
                break
    finally:
        state.angles = originals[0]
        state.transforms = originals[1]
    # A successful sampled replay remains diagnostic evidence, never a
    # manufacturing approval. Explicitly distinguish complete vs partial.
    complete = len(sequence) == len(known)
    warning_steps = [
        {"step_number": item["step_number"], "bend_id": item["bend_id"],
         "reason": item["reason"],
         "diagnostic_findings": (
             item.get("stage_b") or {}).get("diagnostic_findings", [])}
        for item in steps if item.get("conditional_retract_warning")
    ]
    if stop_reason:
        conclusion = (
            "STOPPED_AT_STEP_{}: 2D preview rejected or could not validate {}. "
            "Check stage_a/stage_b findings; this is not proof of 3D impossibility."
        ).format(stop_reason["step_number"], stop_reason["bend_id"])
    elif warning_steps:
        conclusion = (
            "CONDITIONAL_RETRACT_WARNING: {}/{} selected bends checked, "
            "{} retract punch interference warning(s); no 3D approval."
        ).format(len(accepted), len(sequence), len(warning_steps))
    elif not complete:
        conclusion = (
            "SELECTED_PREFIX_HAS_NO_SAMPLED_2D_OBSTRUCTION; "
            "unselected bends and 3D physics remain unverified."
        )
    else:
        conclusion = (
            "FULL_MANUAL_ORDER_HAS_NO_SAMPLED_2D_OBSTRUCTION; "
            "continuous 3D collision and machine cycle remain unverified."
        )
    plan_score = score_sequence(accepted, accepted_setups, op_weights)
    # A missing setup can produce an estimated cost but not a complete one.
    # A blocked bend is intentionally excluded from the scored prefix.
    return {
        "operation_weights": op_weights.to_dict(),
        "backgauge_model": backgauge_model.to_dict() if backgauge_model else None,
        "backgauge_requested": backgauge_model is not None,
        "backgauge_target_steps_checked": sum(
            bool((step.get("stage_b") or {}).get("backgauge_checked"))
            for step in steps
        ),
        "backgauge_interbend_motion_checked": False,
        "plan_cost": plan_score.to_dict(),
        "plan_cost_scope": "ACCEPTED_PREFIX_ONLY",
        "plan_cost_selected_order_complete": (
            stop_reason is None and len(accepted) == len(sequence)
        ),
        "schema_version": "smbs.manual_planar_replay.v1",
        "operator_order_complete": complete,
        "algorithm_conclusion": conclusion,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": ("MANUAL_REPLAY_STOPPED" if stop_reason
                   else "CONDITIONAL_RETRACT_WARNING_NOT_3D_VALIDATED"
                   if warning_steps else "SAMPLED_2D_PREFIX_CLEAR_OR_TOUCH"),
        "physical_certification": "NOT_CERTIFIED_3D",
        "selected_sequence": list(sequence),
        "reverse_axis_by_bend": {
            bid: bool(reverse_axis_by_bend.get(bid, False))
            for bid in sequence
        },
        "allow_retract_warning": allow_retract_warning,
        "retract_warnings": warning_steps,
        "total_bends": len(known),
        "accepted_prefix": accepted,
        "remaining_bends": [bid for bid in known if bid not in accepted],
        "first_blocker": stop_reason,
        "steps": steps,
        "elapsed_s": round(perf_counter() - start, 6),
        "sampling": {"bend_fractions": 17, "stroke_fractions": 9,
                     "tolerance_mm": tolerance_mm},
        "warnings": [
            "Sampled planar sections cannot certify continuous motion, 3D tool ends, backgauge, frame, or transfer.",
            "Previous folds (such as B07/P09 before B03) may occupy another axial X station and never appear in the central 2D cutting section; use per-panel native 3D attribution.",
            "2D interference does not prove a collision in the actual 3D model.",
            "End-zone conditional exemption is not applied by manual 2D replay.",
            "Only the explicitly numbered sequence was replayed; unselected bends were not executed.",
            ("RETRACT-only punch section intersections are warnings in this "
             "diagnostic run; they remain physical hazards pending 3D validation."
             if allow_retract_warning else
             "RETRACT intersections remain blocking in this diagnostic run."),
        ],
    }
