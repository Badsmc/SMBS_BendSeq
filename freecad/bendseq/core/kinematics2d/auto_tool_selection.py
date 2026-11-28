"""Preliminary single-station tooling shortlist; no geometric clearance proof."""
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from .segment_assembly_planner import validate_inventory, plan_pair_for_bend, AssemblyPlanningError
from .flange_die_check import positive_mm
from .bending_technology import TechnologyInputs, assess_technology


@dataclass(frozen=True)
class BendRequirement:
    bend_id: str
    length_mm: float
    flange_mm: float | None = None
    thickness_mm: float | None = None
    angle_deg: float | None = None

    def __post_init__(self):
        if not self.bend_id or positive_mm(self.length_mm) is None:
            raise ValueError("Invalid bend requirement")


@dataclass(frozen=True)
class ToolCandidate:
    punch_id: str
    die_id: str
    opening_mm: float | None
    plan: dict
    warnings: tuple[str, ...]
    score: tuple
    status: str = "CANDIDATE_UNVERIFIED"
    punch_reversed: bool | None = None
    technology: dict | None = None


@dataclass(frozen=True)
class SelectionResult:
    candidates: tuple[ToolCandidate, ...]
    rejected: tuple[tuple[str, str, str], ...]
    complete: bool
    examined: int


def shortlist(catalog, bends, clamps, *, required_span_mm=None, allowance_mm=15.0,
              max_results=20, max_pairs=500, budget_s=10.0, cancelled=lambda: False, trace=lambda *a, **k: None,
              technology_inputs=None):
    """Rank common full-coverage segment plans, not bend feasibility.

    A successful plan only proves a candidate inventory/clamp layout. All
    angle, material, force, mounting, pose and collision checks remain pending.
    The time budget is cooperative between planner calls.
    """
    bends = tuple(bends)
    if not bends or max_results < 1 or max_pairs < 1 or not isfinite(budget_s) or budget_s <= 0:
        raise ValueError("Invalid selection input")
    required = max(float(b.length_mm) for b in bends)
    if required_span_mm is not None:
        span = positive_mm(required_span_mm)
        if span is None:
            raise ValueError("Invalid shared stroke span")
        required = max(required, span)
    if not isfinite(allowance_mm) or allowance_mm < 0:
        raise ValueError("Invalid allowance")
    start = monotonic()
    rejected, candidates = [], []
    stock = {}
    for tool in catalog.all_tools():
        key = (tool.kind, tool.tool_id)
        try:
            if not tool.has_sidecar:
                raise AssemblyPlanningError("NO_SIDECAR")
            rows = validate_inventory(tool.metadata.get("segments", []))
            if not rows:
                raise AssemblyPlanningError("NO_SEGMENT_INVENTORY")
            if sum(row["length_mm"] * row["quantity"] for row in rows) + 1e-6 < required:
                raise AssemblyPlanningError("INSUFFICIENT_STOCK")
            stock[key] = rows
        except (AssemblyPlanningError, TypeError, ValueError) as exc:
            rejected.append((tool.tool_id if tool.kind == "punch" else "",
                             tool.tool_id if tool.kind == "die" else "", str(exc)))
    trace('INVENTORY_FILTER', rejected=tuple(rejected), accepted=tuple(stock), required_mm=required)
    count = 0
    complete = True
    for punch in catalog.punches:
        p = stock.get(("punch", punch.tool_id))
        if p is None:
            continue
        for die in catalog.dies:
            d = stock.get(("die", die.tool_id))
            if d is None:
                continue
            if count >= max_pairs or monotonic() - start >= budget_s or cancelled():
                complete = False
                break
            count += 1
            pair_started = monotonic()
            trace('PAIR_STARTED', punch=punch.tool_id, die=die.tool_id, required_mm=required)
            opening = positive_mm(die.metadata.get("v_width_mm"))
            if opening is None:
                opening = positive_mm(die.metadata.get("v_opening_mm"))
            warnings = ["TECHNOLOGY_AND_COLLISIONS_NOT_CHECKED" if technology_inputs is None else "COLLISIONS_NOT_CHECKED"]
            technology = None
            if technology_inputs is not None:
                technology = assess_technology(bends, opening, punch.metadata, die.metadata, technology_inputs)
                trace("TECHNOLOGY_PAIR", punch=punch.tool_id, die=die.tool_id, report=technology)
                warnings.extend(technology["warnings"])
                if technology["blocked"]:
                    rejected.append((punch.tool_id, die.tool_id, "; ".join(technology["blocked"])))
                    continue
            if opening is None:
                warnings.append("V_OPENING_UNKNOWN")
            for bend in bends:
                flange = positive_mm(bend.flange_mm)
                if flange is None:
                    warnings.append("FLANGE_UNKNOWN: " + bend.bend_id)
                elif opening is not None and flange < opening - 1e-7:
                    # Existing nominal full-V warning is intentionally advisory.
                    warnings.append("FLANGE_BELOW_NOMINAL_V: " + bend.bend_id)
            try:
                plan = plan_pair_for_bend(p, d, required, clamps,
                                          allowance_mm=allowance_mm, full_coverage=True)
            except (AssemblyPlanningError, ValueError) as exc:
                rejected.append((punch.tool_id, die.tool_id, str(exc)))
                trace('PAIR_ERROR', punch=punch.tool_id, die=die.tool_id, error=str(exc), elapsed_s=monotonic()-pair_started)
                continue
            trace('PAIR_LAYOUT', punch=punch.tool_id, die=die.tool_id, plan=plan, warnings=warnings, elapsed_s=monotonic()-pair_started)
            if plan.get("status") != "ASSEMBLY_CANDIDATE_UNVERIFIED":
                rejected.append((punch.tool_id, die.tool_id, plan.get("status", "UNKNOWN")))
                continue
            score = (len(warnings), len(plan["punch_segments"]) + len(plan["die_segments"]),
                     plan["punch_total_mm"] + plan["die_total_mm"] - 2 * required,
                     punch.tool_id, die.tool_id)
            candidates.append(ToolCandidate(punch.tool_id, die.tool_id, opening, plan,
                                            tuple(warnings), score, technology=technology))
        if not complete:
            break
    trace('INVENTORY_FINISHED', complete=complete, examined=count, rejected=rejected, candidate_count=len(candidates), elapsed_s=monotonic()-start)
    return SelectionResult(tuple(sorted(candidates, key=lambda c: c.score)[:max_results]),
                           tuple(rejected), complete, count)
