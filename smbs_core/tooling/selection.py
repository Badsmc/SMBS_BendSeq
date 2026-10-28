"""
selection.py - Optimal tooling selection (Requirement 4, Phase E).

Post-process assignment of punch/die + segment composition per bend,
minimizing tool changes (Q3=A) subject to:
  - clamp constraints (Requirement 4.1, validate_punch_chain)
  - segment composition rule (Q11=C: bend_line_length - 10..15mm)
  - tool compatibility (tip_radius, V-opening, angle)

Does NOT touch search (Phase 6a CLOSED). Pure function, no I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig,
    PunchSegment,
    validate_punch_chain,
)
from smbs_core.geometry_backend.io.tooling_catalog import ToolDescriptor


class ToolingSelectionError(ValueError):
    """Raised on invalid inputs or unrecoverable selection failure."""


# Q11=C segment composition tolerance per ROADMAP §11 Q2 rule 5:
# punch length = bend_line_length - 10..15mm.
SEGMENT_TARGET_OFFSET_MM = 12.5   # midpoint of 10..15
SEGMENT_TARGET_TOLERANCE_MM = 2.5

# Compatibility tolerances (loose; concrete values from existing tool_database.py heuristics)
V_WIDTH_RATIO_TARGET = 6.0         # V ≈ 6 * T
V_WIDTH_RATIO_TOLERANCE = 0.25     # ±25% relative
TIP_RADIUS_TOLERANCE_RATIO = 1.2   # punch.tip_radius <= bend_radius * 1.2


def select_tooling(
    candidate_sequence: Sequence[Dict[str, Any]],
    bends_map: Dict[str, Dict[str, Any]],
    clamps_config: Optional[ClampConfig],
    available_punches: Sequence[ToolDescriptor],
    available_dies: Sequence[ToolDescriptor],
    segment_catalog: Sequence[float],
    objective: str = "MIN_TOOL_CHANGES",
) -> Dict[str, Any]:
    """
    Returns payload dict matching tooling_selection.schema.json 'payload' section:
        {objective, assignments, total_tool_changes, warnings}

    Caller (run_pipeline, S.30c) wraps in full envelope (schema_name, run_id, ...).

    Raises:
        ToolingSelectionError:
          - clamps_config is None (invariant #3: enforcement requires config)
          - segment_catalog empty / non-positive
          - objective not in {MIN_TOOL_CHANGES, MIN_SEGMENT_COUNT, MIN_COST}
          - candidate_sequence step references bend_id absent from bends_map
    """
    # --- Input validation (raise on invalid, no silent defaults) ---
    if clamps_config is None:
        raise ToolingSelectionError(
            "Clamp enforcement requires clamps_config (Requirement 4.1 + #3)"
        )
    if not segment_catalog or any(s <= 0 for s in segment_catalog):
        raise ToolingSelectionError(
            "segment_catalog must be a non-empty sequence of positive floats"
        )
    if objective not in ("MIN_TOOL_CHANGES", "MIN_SEGMENT_COUNT", "MIN_COST"):
        raise ToolingSelectionError(f"Unsupported objective: {objective}")

    # --- Per-bend candidate enumeration ---
    # For each bend, compute list of (punch, die, segments, clamp_validation)
    # candidates. Empty list => bend cannot be assigned.
    candidates_per_bend: Dict[str, List[Dict[str, Any]]] = {}
    warnings: List[str] = []

    for step in candidate_sequence:
        bend_id = step["bend_id"]
        if bend_id not in bends_map:
            raise ToolingSelectionError(
                f"candidate_sequence bend_id={bend_id!r} not in bends_map"
            )
        bend = bends_map[bend_id]
        candidates_per_bend[bend_id] = _enumerate_candidates(
            bend_id=bend_id,
            bend=bend,
            available_punches=available_punches,
            available_dies=available_dies,
            segment_catalog=segment_catalog,
            clamps_config=clamps_config,
            warnings=warnings,
        )

    # --- DP over sequence (minimize tool changes) ---
    # Order bends by their order in candidate_sequence.
    ordered_bend_ids = [s["bend_id"] for s in candidate_sequence]
    assignments = _dp_assign(
        ordered_bend_ids=ordered_bend_ids,
        candidates_per_bend=candidates_per_bend,
        objective=objective,
    )

    total_tool_changes = sum(
        a["tool_change_count"] for a in assignments
    )
    return {
        "objective": objective,
        "assignments": assignments,
        "total_tool_changes": total_tool_changes,
        "warnings": warnings,
    }


def _enumerate_candidates(
    bend_id: str,
    bend: Dict[str, Any],
    available_punches: Sequence[ToolDescriptor],
    available_dies: Sequence[ToolDescriptor],
    segment_catalog: Sequence[float],
    clamps_config: ClampConfig,
    warnings: List[str],
) -> List[Dict[str, Any]]:
    """
    Returns list of candidate dicts:
        {punch_id, die_id, segment_composition, clamp_validation}
    Empty list means no valid candidate for this bend.
    """
    bend_length = _require_positive_number(bend, "length_mm", bend_id)
    bend_radius = _require_positive_number(bend, "radius_mm", bend_id)
    bend_angle = _require_number(bend, "angle_deg", bend_id)
    thickness = _require_positive_number(bend, "thickness_mm", bend_id)

    target_length = bend_length - SEGMENT_TARGET_OFFSET_MM
    if target_length <= 0:
        warnings.append(
            f"bend {bend_id}: bend_length {bend_length}mm too short for segment rule"
        )
        return []

    candidates: List[Dict[str, Any]] = []

    for punch in available_punches:
        punch_radius = punch.metadata.get("radius_mm")
        if punch_radius is None:
            continue  # no silent default; skip
        if punch_radius > bend_radius * TIP_RADIUS_TOLERANCE_RATIO:
            continue
        punch_angle = punch.metadata.get("angle_deg")
        if punch_angle is not None and abs(punch_angle - bend_angle) > 1.0:
            continue

        for die in available_dies:
            die_v = die.metadata.get("v_width_mm")
            if die_v is None:
                continue
            target_v = V_WIDTH_RATIO_TARGET * thickness
            if abs(die_v - target_v) > target_v * V_WIDTH_RATIO_TOLERANCE:
                continue
            die_angle = die.metadata.get("angle_deg")
            if die_angle is not None and abs(die_angle - bend_angle) > 1.0:
                continue

            segments = _compose_segments(target_length, segment_catalog)
            if segments is None:
                continue

            punch_segments = [
                PunchSegment(start_mm=s["start_mm"], end_mm=s["end_mm"])
                for s in segments
            ]
            clamp_res = validate_punch_chain(punch_segments, clamps_config)
            if not clamp_res.valid:
                continue

            candidates.append({
                "punch_id": punch.tool_id,
                "die_id": die.tool_id,
                "segment_composition": segments,
                "clamp_validation": {
                    "valid": True,
                    "errors": list(clamp_res.errors),
                    "warnings": list(clamp_res.warnings),
                },
            })

    if not candidates:
        warnings.append(
            f"bend {bend_id}: no valid punch/die/segment/clamp combination"
        )
    return candidates


def _compose_segments(
    target_length: float,
    segment_catalog: Sequence[float],
) -> Optional[List[Dict[str, Any]]]:
    """
    Greedy compose segments (largest first) to cover target_length within
    SEGMENT_TARGET_TOLERANCE_MM. Returns None if cannot cover.

    Returns list of {length_mm, start_mm, end_mm, segment_id?}.
    """
    sorted_catalog = sorted(segment_catalog, reverse=True)
    lo = target_length - SEGMENT_TARGET_TOLERANCE_MM
    hi = target_length + SEGMENT_TARGET_TOLERANCE_MM

    # Try greedy largest-first
    composed: List[float] = []
    total = 0.0
    for length in sorted_catalog:
        while total + length <= hi:
            composed.append(length)
            total += length
            if total >= lo:
                break
        if total >= lo:
            break

    if total < lo:
        return None

    result: List[Dict[str, Any]] = []
    cursor = 0.0
    for length in composed:
        result.append({
            "length_mm": length,
            "start_mm": cursor,
            "end_mm": cursor + length,
        })
        cursor += length
    return result


def _dp_assign(
    ordered_bend_ids: List[str],
    candidates_per_bend: Dict[str, List[Dict[str, Any]]],
    objective: str,
) -> List[Dict[str, Any]]:
    """
    DP over sequence: dp[i][(punch_id, die_id)] = min cost up to bend i.
    Cost = 0 if same (punch, die) as previous, else 1.
    Returns assignments list; bends with no candidates are skipped (not in list).
    """
    # Filter to only bends that have candidates
    valid_bends = [bid for bid in ordered_bend_ids if candidates_per_bend.get(bid)]
    if not valid_bends:
        return []

    # dp[step_idx] maps (punch_id, die_id) -> (min_cost, backpointer, candidate_dict)
    # where backpointer is the (prev_punch_id, prev_die_id) from previous step
    dp_layers: List[Dict[Tuple[str, str], Tuple[int, Optional[Tuple[str, str]], Dict[str, Any]]]] = []

    # Base step (first valid bend)
    first_bid = valid_bends[0]
    first_layer: Dict[Tuple[str, str], Tuple[int, Optional[Tuple[str, str]], Dict[str, Any]]] = {}
    for cand in candidates_per_bend[first_bid]:
        pair = (cand["punch_id"], cand["die_id"])
        # Cost at initial step is 0 tool changes
        first_layer[pair] = (0, None, cand)
    dp_layers.append(first_layer)

    # Subsequent steps
    for step_idx in range(1, len(valid_bends)):
        bid = valid_bends[step_idx]
        prev_layer = dp_layers[step_idx - 1]
        curr_layer: Dict[Tuple[str, str], Tuple[int, Optional[Tuple[str, str]], Dict[str, Any]]] = {}

        for cand in candidates_per_bend[bid]:
            pair = (cand["punch_id"], cand["die_id"])
            best_cost = float("inf")
            best_prev_pair = None

            for prev_pair, (prev_cost, _, _) in prev_layer.items():
                transition_cost = 0 if pair == prev_pair else 1
                total_cost = prev_cost + transition_cost
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_prev_pair = prev_pair

            if best_cost < float("inf"):
                curr_layer[pair] = (int(best_cost), best_prev_pair, cand)

        if not curr_layer:
            # Fallback if somehow no transitions possible
            break
        dp_layers.append(curr_layer)

    # Backtrack from final layer
    last_layer = dp_layers[-1]
    best_final_pair = min(last_layer.keys(), key=lambda p: last_layer[p][0])

    chosen_candidates: List[Tuple[str, Dict[str, Any]]] = []
    curr_pair: Optional[Tuple[str, str]] = best_final_pair

    for step_idx in range(len(dp_layers) - 1, -1, -1):
        bid = valid_bends[step_idx]
        cost, prev_pair, cand = dp_layers[step_idx][curr_pair]  # type: ignore[index]
        chosen_candidates.append((bid, cand))
        curr_pair = prev_pair

    chosen_candidates.reverse()

    # Build final assignments matching contract
    assignments: List[Dict[str, Any]] = []
    prev_punch_id: Optional[str] = None
    prev_die_id: Optional[str] = None

    for bid, cand in chosen_candidates:
        punch_id = cand["punch_id"]
        die_id = cand["die_id"]

        if prev_punch_id is None:
            punch_changed = False
            die_changed = False
            tool_change_count = 0
        else:
            punch_changed = (punch_id != prev_punch_id)
            die_changed = (die_id != prev_die_id)
            tool_change_count = 1 if (punch_changed or die_changed) else 0

        prev_punch_id = punch_id
        prev_die_id = die_id

        assignments.append({
            "bend_id": bid,
            "punch_id": punch_id,
            "die_id": die_id,
            "segment_composition": cand["segment_composition"],
            "clamp_validation": cand["clamp_validation"],
            "changes_from_previous": {
                "punch_changed": punch_changed,
                "die_changed": die_changed,
            },
            "tool_change_count": tool_change_count,
        })

    return assignments


def _require_number(bend: Dict[str, Any], key: str, bend_id: str) -> float:
    if key not in bend:
        raise ToolingSelectionError(f"bend {bend_id}: missing required field {key!r}")
    return float(bend[key])


def _require_positive_number(bend: Dict[str, Any], key: str, bend_id: str) -> float:
    v = _require_number(bend, key, bend_id)
    if v <= 0:
        raise ToolingSelectionError(
            f"bend {bend_id}: {key!r} must be positive, got {v}"
        )
    return v
