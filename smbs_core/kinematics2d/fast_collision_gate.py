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
            name, value = chunk.rsplit(":", 1)[-1].split("=", 1)
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
        # The intended active zone legitimately contacts both tools.
        # Require a rigid panel OR a distinct completed bend zone.
        if not (component.startswith("panel:")
                or component.startswith("other_zone:")):
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
