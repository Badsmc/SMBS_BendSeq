"""Sequential sampled-2D diagnostic replay of an operator-defined bend order.

Uses the same Stage A / Stage B screens as automated planar search. This is
diagnostic evidence only: it does not approve physical 3D manufacturability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Sequence

from .preliminary_screening import (
    ScreeningDecision, screen_candidate_stage_a, screen_candidate_stage_b,
)
from .sequence_search_2d import _classify_step


def evaluate_manual_planar_order(
    session: Any,
    tooling: Any,
    selected_order: Sequence[str],
    *,
    match_active_bend_length: bool = True,
    tolerance_mm: float = 0.02,
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
    try:
        for bid in sequence:
            step_start = perf_counter()
            state.reset_all()
            for previous in accepted:
                state.set_angle(previous, float(state.bends[previous].angle_deg))
            record = {
                "step_number": len(accepted) + 1,
                "bend_id": bid,
                "angle_deg": float(state.bends[bid].angle_deg),
                "completed_before_step": list(accepted),
            }
            try:
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
                    )
                    record["stage_b"] = stage_b.to_dict()
                    verdict = _classify_step(stage_b)
                    reason = stage_b.reason
                record["evidence"] = verdict
                record["reason"] = reason
                record["elapsed_s"] = round(perf_counter() - step_start, 6)
                steps.append(record)
                if verdict != "SAMPLED_2D_CLEAR_OR_TOUCH":
                    stop_reason = {
                        "step_number": record["step_number"],
                        "bend_id": bid, "evidence": verdict, "reason": reason,
                    }
                    break
                accepted.append(bid)
            except Exception as exc:
                record["evidence"] = "UNVERIFIED_2D"
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
    return {
        "schema_version": "smbs.manual_planar_replay.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": ("SAMPLED_2D_PREFIX_CLEAR_OR_TOUCH"
                   if stop_reason is None else "MANUAL_REPLAY_STOPPED"),
        "physical_certification": "NOT_CERTIFIED_3D",
        "selected_sequence": list(sequence),
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
            "2D interference does not prove a collision in the actual 3D model.",
            "End-zone conditional exemption is not applied by manual 2D replay.",
            "Only the explicitly numbered sequence was replayed; unselected bends were not executed.",
        ],
    }
