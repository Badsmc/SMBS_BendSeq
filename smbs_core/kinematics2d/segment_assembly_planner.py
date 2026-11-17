"""Inventory-bounded punch/die segment assembly planning.

Uses each tool's own .tooling.json inventory, then validates real clamp widths,
spacings and joint support with the existing clamp_validator. Does not assert
that a proposed assembly has passed native OCC or can be mounted physically.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from itertools import combinations
from pathlib import Path

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig, PunchSegment, validate_punch_chain,
)


class AssemblyPlanningError(ValueError):
    pass


def validate_inventory(rows):
    if not isinstance(rows, (list, tuple)):
        raise AssemblyPlanningError("segments must be a list")
    seen = set()
    result = []
    for item in rows:
        if not isinstance(item, dict):
            raise AssemblyPlanningError("segment row must be an object")
        try:
            length = float(item["length_mm"])
            quantity = item["quantity"]
        except (KeyError, ValueError, TypeError) as exc:
            raise AssemblyPlanningError("segment length and quantity required") from exc
        if (not math.isfinite(length) or length <= 0 or length > 10000
                or isinstance(quantity, bool) or not isinstance(quantity, int)
                or quantity < 0 or quantity > 10000):
            raise AssemblyPlanningError("invalid segment length or quantity")
        key = round(length, 6)
        if key in seen:
            raise AssemblyPlanningError("duplicate segment length")
        seen.add(key)
        if quantity:
            result.append({"length_mm": length, "quantity": quantity})
    return sorted(result, key=lambda item: -item["length_mm"])


def read_inventory(fcstd_path):
    """Read only inventory metadata, without modifying other FCStd datums."""
    fcstd_path = Path(fcstd_path)
    sidecar = fcstd_path.with_suffix(".tooling.json")
    if not sidecar.is_file():
        return []
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    return validate_inventory((data.get("metadata") or {}).get("segments", []))


def write_inventory(fcstd_path, rows):
    fcstd_path = Path(fcstd_path)
    if not fcstd_path.is_file():
        raise AssemblyPlanningError("FCStd source missing")
    rows = validate_inventory(rows)
    sidecar = fcstd_path.with_suffix(".tooling.json")
    if not sidecar.is_file():
        raise AssemblyPlanningError("tooling sidecar missing: " + str(sidecar))
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if data.get("kind") not in ("punch", "die") or data.get("id") != fcstd_path.stem:
        raise AssemblyPlanningError("sidecar tool identity mismatch")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise AssemblyPlanningError("metadata must be an object")
    metadata["segments"] = rows
    tmp = sidecar.with_suffix(".tooling.json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, sidecar)
    finally:
        if tmp.exists():
            tmp.unlink()
    return rows


def _segment_combinations(rows, required, allowance, max_states=120000):
    """Bounded finite-inventory search; never invent stock or silently truncate."""
    rows = validate_inventory(rows)
    if not rows:
        return []
    if not math.isfinite(required) or required <= 0:
        raise AssemblyPlanningError("invalid target bend length")
    if not math.isfinite(allowance) or allowance < 0:
        raise AssemblyPlanningError("invalid length allowance")
    solutions = []
    visited = 0

    def search(index, total, selected):
        nonlocal visited
        visited += 1
        if visited > max_states:
            raise AssemblyPlanningError("segment_search_limit_exceeded")
        if required - 1e-6 <= total <= required + allowance + 1e-6:
            solutions.append((round(total, 6), tuple(selected)))
            return
        if index == len(rows) or total > required + allowance + 1e-6:
            return
        item = rows[index]
        for n in range(min(item["quantity"],
                           int((required + allowance - total) /
                               item["length_mm"] + 1e-8)), -1, -1):
            search(index + 1, total + n * item["length_mm"],
                   selected + [(item["length_mm"], n)] if n else selected)
    search(0, 0., [])
    solutions.sort(key=lambda item: (item[0] - required,
                                    sum(n for _, n in item[1]), item[1]))
    return solutions[:128]


def _clamp_candidates(chain, config):
    """Fixed clamps are mandatory; movable clamps receive explicit proposals.

    A proposed movable placement is labeled as such, not claimed as verified
    machine mounting. Respect width + minimum edge gap for every pair.
    """
    if config.positions_mm:
        return [(tuple(config.positions_mm), False)]
    if not config.repositionable:
        return []
    start, end = chain[0].start_mm, chain[-1].end_mm
    width = config.width_mm
    min_pitch = width + config.gap_between_mm
    # Candidate centers are derived from joint positions and evenly spaced
    # supports; the machine's absolute clamp travel is not modeled.
    joints = [s.end_mm for s in chain[:-1]]
    candidates = sorted(set(round(x, 3) for x in (
        joints + [start + width / 2., end - width / 2.,
                  start + (end-start)*0.25, start + (end-start)*0.75,
                  (start+end)/2, start, end]
    )))
    count = config.min_clamps_under_punch
    if count > 5 or count > len(candidates):
        return []
    layouts = []
    for selected in combinations(candidates, count):
        if any(selected[i+1]-selected[i] < min_pitch-1e-6
               for i in range(len(selected)-1)):
            continue
        layouts.append((selected, True))
    return layouts


def _validate_clamps(chain, config):
    if not isinstance(config, ClampConfig):
        raise AssemblyPlanningError("clamp configuration required")
    for centers, proposed in _clamp_candidates(chain, config):
        # Check all clamp blocks, not only their support under the chain.
        if any(centers[i+1]-centers[i] <
               config.width_mm + config.gap_between_mm - 1e-6
               for i in range(len(centers)-1)):
            continue
        candidate = replace(config, positions_mm=tuple(centers))
        validity = validate_punch_chain(chain, candidate)
        if validity.valid:
            return {"centers_mm": list(centers),
                    "width_mm": float(config.width_mm),
                    "gap_between_mm": float(config.gap_between_mm),
                    "proposed_reposition": proposed, "status": (
                        "PROPOSED_REPOSITION_UNVERIFIED" if proposed
                        else "CONFIGURED_CLAMP_LAYOUT_VALID")}
    return None


def plan_pair_for_bend(punch_inventory, die_inventory, bend_length_mm,
                       clamps: ClampConfig, allowance_mm=15.0):
    """Find a stock-bounded assembled punch+die for a single bend.

    Exact bend length is preferred. Surplus <= allowance_mm is permitted and
    displayed; no claim of full tool/part collision-free assembly is made.
    """
    p_solutions = _segment_combinations(punch_inventory, bend_length_mm,
                                        allowance_mm)
    d_solutions = _segment_combinations(die_inventory, bend_length_mm,
                                        allowance_mm)
    if not p_solutions or not d_solutions:
        return {"status": "NO_INVENTORY_COMBINATION",
                "required_mm": bend_length_mm}
    for p_total, p_counts in p_solutions:
        # Try segment ordering with joints under clamp blocks; same-length
        # pieces interchangeable. Limit permutations for predictable runtime.
        from itertools import permutations
        expanded = [length for length, count in p_counts
                    for _ in range(count)]
        if len(expanded) > 12:
            continue
        for lengths in list(dict.fromkeys(permutations(expanded))):
            cursor = -p_total / 2.
            chain = []
            for length in lengths:
                chain.append(PunchSegment(cursor, cursor + length))
                cursor += length
            clamp_plan = _validate_clamps(chain, clamps)
            if clamp_plan is None:
                continue
            d_total, d_counts = d_solutions[0]
            return {
                "status": "ASSEMBLY_CANDIDATE_UNVERIFIED",
                "required_mm": float(bend_length_mm),
                "punch_total_mm": p_total,
                "die_total_mm": d_total,
                "punch_segments": [
                    {"length_mm": length, "start_x_mm": seg.start_mm,
                     "end_x_mm": seg.end_mm}
                    for length, seg in zip(lengths, chain)],
                "die_segments": [
                    {"length_mm": length, "quantity": count}
                    for length, count in d_counts],
                "clamps": clamp_plan,
                "limitations": ("No native OCC collision certification; "
                                "movable clamp travel and mounting unverified"),
            }
    return {"status": "NO_CLAMP_COMPATIBLE_ASSEMBLY",
            "required_mm": bend_length_mm}
