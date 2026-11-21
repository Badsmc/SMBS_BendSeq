"""Conservative *search-only* coarse tool obstruction gate for FAST.

Stage B samples the *selected punch/die* in the active machine cutting
plane at ram APPROACH, BENDING, BOTTOM and RETRACT. Only observed
interior-material PUNCH/DIE penetration in a well-defined active phase may
block a specific (completed operations mask, next op ID, 0/180 flag).

The block is provisional 2D search evidence, NOT proof of 3D infeasibility:
a different machine station, tool section, or real 3D placement may repair
the pose. Never extrapolate to an entire bend ID or prune from an UNKNOWN,
TOUCH, TIME_BUDGET, RETRACT, frame, or backgauge finding.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


# A boundary crossing or strictly interior material-in-material placement is
# qualitatively stronger than mere BBoxes, near contact, or numerical touches.
_INTERIOR_REASON = frozenset((
    "proper_material_boundary_crossing",
    "sheet_in_tool_material",
    "tool_in_sheet_material",
))
_TOOL_NAMES = frozenset(("PUNCH", "DIE"))
_BLOCK_PHASES = frozenset(("APPROACH", "BENDING", "BOTTOM"))


@dataclass(frozen=True)
class CoarseToolGate:
    forbidden_transitions: tuple[tuple[int, str, bool], ...]
    observed_obstructions: tuple[dict, ...]
    provisional_only: bool = True

    def to_dict(self) -> dict:
        return {
            "forbidden_transitions": [{
                "completed_mask": mask,
                "operation_id": op,
                "reverse_axis": reverse,
            } for mask, op, reverse in self.forbidden_transitions],
            "observed_obstructions": [dict(item)
                                      for item in self.observed_obstructions],
            "count": len(self.forbidden_transitions),
            "scope": "FAST_SEARCH_ONLY_COARSE_SAMPLED_2D_NOT_3D_PROOF",
            "provisional_only": self.provisional_only,
        }


def _parse_tool_contact(issue: str):
    """Inspect structured Stage B diagnostic fields; never guess a tool."""
    fields = {}
    # The first segment includes the operation prefix (e.g. B01:tool=PUNCH).
    for chunk in str(issue).split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            name = key.rsplit(":", 1)[-1]
            if name in ("tool", "phase", "component", "contact"):
                fields[name] = value
    return fields


def coarse_tool_gate(screen: Any) -> CoarseToolGate:
    """Recognize only actual material penetration with selected tools.

    2D Stage B errors and missing data can never produce a forbidden edge.
    Only the reported first blocked state is gated. A different folded mask
    or the opposite loading orientation is still eligible for FAST search.
    """
    found: dict[tuple[int, str, bool], dict] = {}
    for check in getattr(screen, "checks", ()):
        if getattr(check, "status", None) != "POTENTIAL_2D_INTERFERENCE":
            continue
        transition = getattr(check, "failed_transition", None)
        if (not isinstance(transition, tuple) or len(transition) != 3
                or type(transition[0]) is not int
                or not isinstance(transition[1], str)
                or type(transition[2]) is not bool):
            continue
        fields = _parse_tool_contact(getattr(check, "first_issue", ""))
        if (fields.get("tool") not in _TOOL_NAMES
                or fields.get("phase") not in _BLOCK_PHASES
                or fields.get("contact") not in _INTERIOR_REASON):
            continue
        component = fields.get("component", "")
        # Active fixed/moving panels can be in legitimate forming
        # contact, while the *other* already-bent panels are the high
        # confidence access blockers sought here. The existing machine
        # section reports those panels as bare IDs like "P09", not
        # necessarily "panel:P09".
        other_formed_material = bool(
            component.startswith("panel:")
            or re.fullmatch(r"P[0-9]+", component)
            or component.startswith("other_zone:")
        )
        # A parked/raised PUNCH may not already penetrate the active
        # rigid flange before the bend starts: this is a blocked
        # approach, not an intended punch-to-bend contact.
        active_panel_approach = (
            component in ("active_fixed", "active_moving")
            and fields["phase"] == "APPROACH"
            and fields["tool"] == "PUNCH"
        )
        if not (other_formed_material or active_panel_approach):
            continue
        found[transition] = {
            "completed_mask": transition[0],
            "operation_id": transition[1],
            "reverse_axis": transition[2],
            "tool": fields["tool"],
            "phase": fields["phase"],
            "component": component,
            "contact": fields["contact"],
            "meaning": "COARSE_2D_FILTER_ONLY_REQUIRES_NATIVE_3D_FOR_PROOF",
        }
    keys = tuple(sorted(found))
    return CoarseToolGate(keys, tuple(found[key] for key in keys))


@dataclass(frozen=True)
class CoarseRerunReport:
    candidates: tuple
    gate: CoarseToolGate
    status: str
    elapsed_seconds: float
    pruned_expansions: int
    explored_states: int

    def to_dict(self):
        return {
            "new_candidates": [candidate.to_dict()
                               for candidate in self.candidates],
            "gate": self.gate.to_dict(),
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 5),
            "pruned_expansions": self.pruned_expansions,
            "explored_states": self.explored_states,
            "scope": "GENERATED_AVOIDING_KNOWN_SAMPLED_2D_TOOL_PENETRATIONS",
            "evidence": "UNVERIFIED_3D_NOT_CHECKED",
        }


def rerun_fast_avoiding_tool_collisions(
    fold_state,
    operations,
    existing_candidates,
    panel_vertices_xy,
    screen,
    *,
    weights=None,
    fixed_orientations=None,
    limit_seconds=1.5,
    max_candidates=8,
    search_fn=None,
):
    """Rerun the full order search, excluding only known suspect 2D poses.

    This is NOT a 3D proof: unknown poses remain eligible. Candidate
    generation is fully separate from the Stage B geometry sampling.
    No expensive tool geometry executes within a Beam Search expansion.
    """
    from time import perf_counter
    from .fast_bfm_beam_search import search_fast_candidates
    from .fast_multiside_feedback import transition_feedback_penalties

    started = perf_counter()
    gate = coarse_tool_gate(screen)
    if not gate.forbidden_transitions:
        return CoarseRerunReport(
            (), gate, "NO_STRONG_2D_TOOL_OBSTRUCTIONS",
            perf_counter()-started, 0, 0,
        )
    already = {
        (candidate.operation_ids, candidate.orientation_by_operation)
        for candidate in existing_candidates
    }
    search_fn = search_fn or search_fast_candidates
    result = search_fn(
        fold_state, operations,
        fixed_orientations=fixed_orientations,
        panel_vertices_xy=panel_vertices_xy,
        weights=weights,
        transition_penalties=transition_feedback_penalties(screen),
        forbidden_transitions=gate.forbidden_transitions,
        limit_seconds=limit_seconds,
        beam_width=32,
        max_candidates=max_candidates,
        screen_tool_center_axis=False,
    )
    unique = tuple(
        candidate for candidate in result.candidates
        if (candidate.operation_ids, candidate.orientation_by_operation)
        not in already
    )
    return CoarseRerunReport(
        unique, gate, result.status, perf_counter()-started,
        int(getattr(result, "coarse_2d_pruned_expansions", 0)),
        int(getattr(result, "explored_states", 0)),
    )
