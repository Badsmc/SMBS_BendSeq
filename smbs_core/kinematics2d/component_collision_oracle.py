"""Conservative exact component collision screen (experimental).

All inputs MUST already be in the same machine coordinate frame. This layer
cannot certify a full sheet, continuous path or Conditional tool relief.
Only a proved component/tool intersection above the existing strict volume
tolerance is decisive. Everything else delegates to the full-sheet oracle.
"""
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any, Iterable, Tuple

from smbs_core.kinematics2d.native_collision_3d import (
    compute_aabb_gap, _evaluate_common_volume,
)


class ComponentVerdict(str, Enum):
    PROVEN_PENETRATION = "PROVEN_PENETRATION"
    FALLBACK_REQUIRED = "FALLBACK_REQUIRED"


@dataclass(frozen=True)
class ComponentHit:
    component_id: str
    tool_id: str
    volume_mm3: float


@dataclass(frozen=True)
class ComponentScreenResult:
    verdict: ComponentVerdict
    reason: str
    hit: ComponentHit | None = None
    candidates: int = 0
    aabb_rejected: int = 0
    common_calls: int = 0


def screen_component_penetration(
    components: Iterable[Tuple[str, Any]],
    tools: Iterable[Tuple[str, Any]],
    *,
    volume_tolerance_mm3: float,
) -> ComponentScreenResult:
    """Prove a real collision from ONE component, or require the legacy oracle.

    Never returns CLEAR: individual non-hits do not establish sheet integrity,
    exact near/contact classification, or the global sum of small overlaps.
    Conditional end-zone policy MUST be evaluated by the legacy oracle.
    Component shapes must be the authoritative BRep sheet components, not a
    mesh approximation, and component/tool placements must match exactly.
    """
    if not isfinite(volume_tolerance_mm3) or volume_tolerance_mm3 < 0:
        return ComponentScreenResult(ComponentVerdict.FALLBACK_REQUIRED, "invalid_tolerance")
    try:
        component_items = list(components)
        tool_items = list(tools)
    except Exception:
        return ComponentScreenResult(ComponentVerdict.FALLBACK_REQUIRED, "unreadable_inputs")
    if not component_items or not tool_items:
        return ComponentScreenResult(ComponentVerdict.FALLBACK_REQUIRED, "missing_components_or_tools")
    candidate_count = 0
    rejected = 0
    common_calls = 0
    seen_components = set()
    try:
        for component_id, shape in component_items:
            if not isinstance(component_id, str) or not component_id or component_id in seen_components:
                raise ValueError("invalid_component_id")
            seen_components.add(component_id)
            if shape is None or shape.isNull():
                raise ValueError("invalid_component_shape")
            # Checking expensive isValid here would defeat the intended screen;
            # a validated component representation is a precondition, and no
            # definitive rejection follows from a malformed common result.
            for tool_id, tool in tool_items:
                if not isinstance(tool_id, str) or not tool_id or tool is None or tool.isNull():
                    raise ValueError("invalid_tool")
                gap = compute_aabb_gap(shape.BoundBox, tool.BoundBox)
                if gap is None or not isfinite(gap):
                    raise ValueError("invalid_aabb")
                if gap > 0:
                    rejected += 1
                    continue
                candidate_count += 1
                common_calls += 1
                volume = float(_evaluate_common_volume(shape, tool))
                if not isfinite(volume) or volume < 0:
                    raise ValueError("invalid_common_volume")
                if volume > volume_tolerance_mm3:
                    return ComponentScreenResult(
                        ComponentVerdict.PROVEN_PENETRATION,
                        "exact_component_common_above_tolerance",
                        ComponentHit(component_id, tool_id, volume),
                        candidate_count, rejected, common_calls,
                    )
    except Exception as exc:
        return ComponentScreenResult(
            ComponentVerdict.FALLBACK_REQUIRED,
            "uncertain_component_screen:" + type(exc).__name__,
            None, candidate_count, rejected, common_calls,
        )
    return ComponentScreenResult(
        ComponentVerdict.FALLBACK_REQUIRED,
        "no_individually_proven_blocker",
        None, candidate_count, rejected, common_calls,
    )
