"""Inventory-bounded punch/die segment assembly planning.

Uses each tool's own .tooling.json inventory, then validates real clamp widths,
spacings and joint support with the existing clamp_validator. Does not assert
that a proposed assembly has passed native OCC or can be mounted physically.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, replace
from itertools import combinations, pairwise
from pathlib import Path
from tempfile import NamedTemporaryFile

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig, PunchSegment, validate_punch_chain,
)
from .tooling_assembly import (
    CONTACT_EPS_MM, MAX_PUNCH_SHORTFALL_MM, clamp_spans_for_centres, punch_segment_supports,
)


_MAX_STOCK_COMBINATIONS = 128
_MAX_PUNCH_ORDERS = 2000


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
    metadata = data.get("metadata") if isinstance(data, dict) else None
    if metadata is not None and not isinstance(metadata, dict):
        raise AssemblyPlanningError("metadata must be an object")
    return validate_inventory((metadata or {}).get("segments", []))


def write_inventory(fcstd_path, rows):
    fcstd_path = Path(fcstd_path)
    if not fcstd_path.is_file():
        raise AssemblyPlanningError("FCStd source missing")
    rows = validate_inventory(rows)
    sidecar = fcstd_path.with_suffix(".tooling.json")
    if not sidecar.is_file():
        raise AssemblyPlanningError("tooling sidecar missing: " + str(sidecar))
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("kind") not in ("punch", "die")
            or data.get("id") != fcstd_path.stem):
        raise AssemblyPlanningError("sidecar tool identity mismatch")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise AssemblyPlanningError("metadata must be an object")
    metadata["segments"] = rows
    tmp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=sidecar.parent,
                                suffix=".tmp", delete=False) as output:
            tmp = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(tmp, sidecar)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return rows


def _segment_combinations(rows, required, allowance, max_states=120000, *, shortfall_mm=0.0):
    """Find combinations throughout the allowed length window."""
    rows = validate_inventory(rows)
    if not rows:
        return [], False
    if not math.isfinite(required) or required <= 0:
        raise AssemblyPlanningError("invalid target bend length")
    if not math.isfinite(allowance) or allowance < 0:
        raise AssemblyPlanningError("invalid length allowance")
    solutions = []
    visited = 0
    limited = False
    lower = required - shortfall_mm
    upper = required + allowance
    stock_remaining = [0.0] * (len(rows) + 1)
    for index in reversed(range(len(rows))):
        item = rows[index]
        stock_remaining[index] = stock_remaining[index + 1] + item["length_mm"] * item["quantity"]

    def search(index, total, selected):
        nonlocal visited, limited
        if visited == max_states:
            limited = True
            return
        visited += 1
        if total + stock_remaining[index] < lower - 1e-6:
            return
        if index == len(rows):
            if selected and lower - 1e-6 <= total <= upper + 1e-6:
                solutions.append((round(total, 6), tuple(selected)))
            return
        item = rows[index]
        max_count = min(item["quantity"], int((upper + 1e-6 - total) / item["length_mm"]))
        for n in range(max_count, -1, -1):
            search(index + 1, total + n * item["length_mm"],
                   selected + [(item["length_mm"], n)] if n else selected)
            if limited:
                break
    search(0, 0., [])
    solutions.sort(key=lambda item: (item[0] - required,
                                    sum(n for _, n in item[1]), item[1]))
    return solutions, limited


def _clamp_candidates(chain, config):
    """Fixed clamps are mandatory; movable clamps receive explicit proposals.

    A proposed movable placement is labeled as such, not claimed as verified
    machine mounting. Respect width + minimum edge gap for every pair.
    """
    if config.centres_mm:
        return [(config.centres_mm, False)]
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
    spans = tuple((seg.start_mm, seg.end_mm) for seg in chain)
    for centers, proposed in _clamp_candidates(chain, config):
        # Check all clamp blocks, not only their support under the chain.
        if any(centers[i+1]-centers[i] <
               config.width_mm + config.gap_between_mm - 1e-6
               for i in range(len(centers)-1)):
            continue
        zones = clamp_spans_for_centres(centers, config.width_mm)
        supports = punch_segment_supports(spans, zones)
        support_numbers = {number for segment in supports for number, _ in segment}
        if len(support_numbers) < config.min_clamps_under_punch or not all(supports):
            continue
        candidate = replace(config, positions_mm=tuple(centers))
        validity = validate_punch_chain(chain, candidate)
        if validity.valid:
            plan = {
                "centers_mm": list(centers),
                "width_mm": float(config.width_mm),
                "gap_between_mm": float(config.gap_between_mm),
                "proposed_reposition": proposed,
                "status": ("PROPOSED_REPOSITION_UNVERIFIED" if proposed
                           else "CONFIGURED_CLAMP_LAYOUT_VALID"),
            }
            return plan, supports
    return None


def _unique_segment_orders(lengths, max_orders=_MAX_PUNCH_ORDERS):
    """Visit descending lexicographic orders of the multiset once each."""
    order = sorted(lengths, reverse=True)
    for _ in range(max_orders):
        yield tuple(order)
        pivot = len(order) - 2
        while pivot >= 0 and order[pivot] <= order[pivot + 1]:
            pivot -= 1
        if pivot < 0:
            return
        swap = len(order) - 1
        while order[swap] >= order[pivot]:
            swap -= 1
        order[pivot], order[swap] = order[swap], order[pivot]
        order[pivot + 1:] = reversed(order[pivot + 1:])


def _punch_shifts(chain, bend_length_mm, zones, config):
    """Test endpoints and intervals where contact, joints or overhang change."""
    yield 0.0
    first, last = chain[0], chain[-1]
    # Keep the shorter span inside the longer one. No lateral shift adds
    # unsupported length beyond the shortfall already selected from stock.
    lo, hi = sorted((bend_length_mm / 2.0 - last.end_mm,
                     -bend_length_mm / 2.0 - first.start_mm))
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if not zones or hi - lo <= 1e-9:
        return

    events = {lo, hi, 0.0}
    tolerance = config.joint_tolerance_mm
    left_overhang = config.overhang_ratio * first.length_mm + tolerance
    right_overhang = config.overhang_ratio * last.length_mm + tolerance

    def add(*values):
        events.update(value for value in values if lo <= value <= hi)

    for left, right in zones:
        add(
            left - last.end_mm - tolerance, right - first.start_mm + tolerance,
            left - first.start_mm - left_overhang, right - last.end_mm + right_overhang,
        )
        for index, segment in enumerate(chain):
            start, end = segment.start_mm, segment.end_mm
            add(
                left - end + CONTACT_EPS_MM, right - start - CONTACT_EPS_MM,
                left - start, right - end,
            )
            if config.joints_must_be_on_clamps and index < len(chain) - 1:
                add(left - end - tolerance, right - end + tolerance)

    boundaries = sorted(events)
    events.update((a + b) / 2.0 for a, b in pairwise(boundaries) if b - a > 1e-9)
    events.remove(0.0)
    yield from sorted(events, key=lambda shift: (abs(shift), shift))


@dataclass(frozen=True)
class _PunchLayout:
    chain: tuple[PunchSegment, ...]
    clamp_plan: dict
    supports: tuple
    shift_mm: float
    short_support: float

    @property
    def rank(self):
        return round(self.short_support, 9), -abs(self.shift_mm)


def _place_punch(lengths, bend_length_mm, config, zones, max_clamp_gap):
    cursor = -math.fsum(lengths) / 2.0
    chain = []
    for length in lengths:
        chain.append(PunchSegment(cursor, cursor + length))
        cursor += length
    short_sections = tuple(i for i, length in enumerate(lengths)
                           if length <= max_clamp_gap + CONTACT_EPS_MM)

    best = None
    for shift in _punch_shifts(chain, bend_length_mm, zones, config):
        shifted = tuple(PunchSegment(seg.start_mm + shift, seg.end_mm + shift) for seg in chain)
        validation = _validate_clamps(shifted, config)
        if validation is None:
            continue
        clamp_plan, supports = validation
        fraction = min(
            (max(overlap for _, overlap in supports[i]) / lengths[i] for i in short_sections),
            default=1.0,
        )
        candidate = _PunchLayout(shifted, clamp_plan, supports, shift, min(1.0, fraction))
        if best is None or candidate.rank > best.rank:
            best = candidate
        if best.rank[0] == 1.0:
            break
    return best


def plan_pair_for_bend(punch_inventory, die_inventory, bend_length_mm,
                       clamps: ClampConfig, allowance_mm=15.0):
    """Find a stock-bounded assembled punch+die for a single bend.

    Prefer an exact punch, then a shorter one within the end shortfall
    allowance, then an overlength set. The die must cover the centred bend.
    """
    if not isinstance(clamps, ClampConfig):
        raise AssemblyPlanningError("clamp configuration required")
    p_solutions, p_limited = _segment_combinations(
        punch_inventory, bend_length_mm, allowance_mm,
        shortfall_mm=min(MAX_PUNCH_SHORTFALL_MM, allowance_mm),
    )

    def fit(total):
        delta = total - bend_length_mm
        return (0, 0.0) if abs(delta) <= CONTACT_EPS_MM else (1 if delta < 0 else 2, abs(delta))

    p_solutions.sort(key=lambda item: fit(item[0]))
    d_solutions, d_limited = _segment_combinations(die_inventory, bend_length_mm, allowance_mm)
    if not p_solutions or not d_solutions:
        missing = ((p_solutions, p_limited), (d_solutions, d_limited))
        exhausted = any(not options and not limited for options, limited in missing)
        status = "NO_INVENTORY_COMBINATION" if exhausted else "ASSEMBLY_SEARCH_LIMIT"
        return {"status": status, "required_mm": bend_length_mm}
    zones = clamp_spans_for_centres(clamps.centres_mm, clamps.width_mm)
    gaps = tuple(right[0] - left[1] for left, right in pairwise(zones))
    if zones and (len(zones) < clamps.min_clamps_under_punch
                  or any(gap < clamps.gap_between_mm - CONTACT_EPS_MM for gap in gaps)):
        return {"status": "NO_CLAMP_COMPATIBLE_ASSEMBLY", "required_mm": bend_length_mm}
    max_clamp_gap = max(gaps, default=0.0)
    search_limited = p_limited or len(p_solutions) > _MAX_STOCK_COMBINATIONS
    selected = None
    for p_total, p_counts in p_solutions[:_MAX_STOCK_COMBINATIONS]:
        if selected is not None and fit(p_total) != fit(selected[0]):
            break
        expanded = [length for length, count in p_counts
                    for _ in range(count)]
        best = None
        orders = _unique_segment_orders(expanded, _MAX_PUNCH_ORDERS + 1)
        for index, lengths in enumerate(orders):
            if index == _MAX_PUNCH_ORDERS:
                search_limited = True
                break
            placed = _place_punch(lengths, bend_length_mm, clamps, zones, max_clamp_gap)
            if placed is not None and (best is None or placed.rank > best.rank):
                best = placed
                if best.rank == (1.0, 0.0):
                    break
        if best is None:
            continue
        if selected is None or best.rank[0] > selected[1].rank[0]:
            selected = p_total, best
        if best.rank[0] == 1.0:
            break

    if selected is None:
        status = "ASSEMBLY_SEARCH_LIMIT" if search_limited else "NO_CLAMP_COMPATIBLE_ASSEMBLY"
        return {"status": status, "required_mm": bend_length_mm}

    p_total, best = selected
    d_total, d_counts = d_solutions[0]
    die_cursor = -d_total / 2.0
    die_positions = []
    for length, count in d_counts:
        for _ in range(count):
            end = die_cursor + length
            die_positions.append({"length_mm": length, "start_x_mm": die_cursor, "end_x_mm": end})
            die_cursor = end
    return {
        "status": "ASSEMBLY_CANDIDATE_UNVERIFIED",
        "required_mm": float(bend_length_mm),
        "punch_total_mm": p_total,
        "punch_center_x_mm": best.shift_mm,
        "punch_shortfall_mm": max(0.0, bend_length_mm - p_total),
        "die_total_mm": d_total,
        "punch_segments": [
            {"length_mm": round(seg.length_mm, 6), "start_x_mm": seg.start_mm,
             "end_x_mm": seg.end_mm, "clamp_support": [
                 {"clamp_number": number, "overlap_mm": overlap}
                 for number, overlap in supports
             ]}
            for seg, supports in zip(best.chain, best.supports)
        ],
        "die_segments": die_positions,
        "clamps": best.clamp_plan,
        "limitations": ("No native OCC collision certification; "
                        "movable clamp travel and mounting unverified"),
    }
