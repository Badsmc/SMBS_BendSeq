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


# Stage X2: conservative MACHINE-frame component screening. Only candidate
# BReps are transformed; separated bounding boxes never enter OCC Common.
from contextlib import contextmanager
from contextvars import ContextVar
from math import sqrt
from types import SimpleNamespace
import time


@dataclass(frozen=True)
class MachineComponentScreen:
    verdict: ComponentVerdict
    reason: str
    tool_label: str
    component_count: int
    aabb_rejected: int
    candidate_count: int
    transformed_count: int
    common_calls: int
    elapsed_ms: float
    hit: ComponentHit | None = None


def _machine_box(part_box, placement):
    """Conservatively enclose a rotated BRep bounding box in machine XYZ.

    8 transformed corners of the authoritative native BRep AABB give a
    superset of the transformed shape; a tiny roundoff pad avoids dropping
    tangent and near-tangent candidates. This is only an *overlap* filter:
    it never qualifies clearance or the physical sheet topology.
    """
    axes = ("X", "Y", "Z")
    bounds = []
    for a in axes:
        lo = float(getattr(part_box, a + "Min"))
        hi = float(getattr(part_box, a + "Max"))
        if not (isfinite(lo) and isfinite(hi) and lo <= hi):
            raise ValueError("invalid_part_bbox")
        bounds.append((lo, hi))
    rot = tuple(tuple(float(x) for x in row) for row in placement.rotation)
    move = tuple(float(x) for x in placement.translation)
    if len(rot) != 3 or any(len(row) != 3 for row in rot) or len(move) != 3:
        raise ValueError("invalid_machine_placement")
    if not all(isfinite(x) for row in rot for x in row) or not all(isfinite(x) for x in move):
        raise ValueError("nonfinite_machine_placement")
    # Reject a scale/shear placement: the existing workpiece uses a rigid
    # FreeCAD transform; the broadphase must use the same physical contract.
    for i in range(3):
        for j in range(3):
            dot = sum(rot[i][k] * rot[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > 1e-5:
                raise ValueError("nonrigid_machine_placement")
    values = [[], [], []]
    for x in bounds[0]:
        for y in bounds[1]:
            for z in bounds[2]:
                v = (x, y, z)
                for axis in range(3):
                    values[axis].append(sum(rot[axis][k] * v[k] for k in range(3)) + move[axis])
    edge = max(abs(x) for axis in values for x in axis)
    pad = max(1e-6, edge * 1e-12)
    return SimpleNamespace(
        XMin=min(values[0]) - pad, XMax=max(values[0]) + pad,
        YMin=min(values[1]) - pad, YMax=max(values[1]) + pad,
        ZMin=min(values[2]) - pad, ZMax=max(values[2]) + pad,
    )


def screen_machine_component_overlap(
    named_part_components,
    placement,
    machine_matrix,
    tool_label,
    tool_shape,
    *,
    volume_tolerance_mm3: float,
    max_exact_candidates: int = 4,
) -> MachineComponentScreen:
    """Inspect native panel/zone solids against ONE real tool in machine XYZ.

    Only a component Common with volume > the normal physical threshold can
    prove penetration. The converse never means safe: every other verdict is
    FALLBACK_REQUIRED, including an AABB separating all components.
    Do not use this alone as an authoritative punch-end-zone classifier.
    """
    started = time.perf_counter()
    total = discarded = candidate_count = transformed = commons = 0
    hit = None
    reason = "uncertain"
    verdict = ComponentVerdict.FALLBACK_REQUIRED
    try:
        components = tuple(named_part_components)
        total = len(components)
        if not total or not isfinite(volume_tolerance_mm3) or volume_tolerance_mm3 < 0:
            raise ValueError("missing_components_or_bad_tolerance")
        if not isinstance(max_exact_candidates, int) or max_exact_candidates < 1:
            raise ValueError("invalid_candidate_limit")
        if tool_shape is None or tool_shape.isNull():
            raise ValueError("missing_tool_shape")
        tool_bbox = tool_shape.BoundBox
        seen = set()
        candidates = []
        for label, part_shape in components:
            if not isinstance(label, str) or not label or label in seen:
                raise ValueError("missing_or_duplicate_component_id")
            seen.add(label)
            if part_shape is None or part_shape.isNull():
                raise ValueError("missing_component_shape")
            # No expensive OCC transformation for a box with strict separation.
            box = _machine_box(part_shape.BoundBox, placement)
            gap = compute_aabb_gap(box, tool_bbox)
            if gap is None or not isfinite(gap):
                raise ValueError("invalid_tool_aabb")
            if gap > 0.0:
                discarded += 1
            else:
                candidates.append((label, part_shape))
        candidate_count = len(candidates)
        if candidate_count > max_exact_candidates:
            reason = "candidate_budget_fallback"
        elif candidate_count == 0:
            reason = "all_aabbs_disjoint_but_topology_unverified"
        else:
            reason = "no_individually_proven_component_penetration"
            for label, part_shape in candidates:
                # Copy means no cached source BRep is ever moved in place.
                moved = part_shape.copy()
                new_shape = moved.transformShape(machine_matrix)
                if new_shape is not None:
                    moved = new_shape
                transformed += 1
                result = screen_component_penetration(
                    ((label, moved),), ((tool_label, tool_shape),),
                    volume_tolerance_mm3=volume_tolerance_mm3,
                )
                commons += result.common_calls
                if result.verdict == ComponentVerdict.PROVEN_PENETRATION:
                    verdict = result.verdict
                    hit = result.hit
                    reason = result.reason
                    break
                if result.reason != "no_individually_proven_blocker":
                    reason = "uncertain_exact_component:" + result.reason
                    break
    except Exception as exc:
        verdict = ComponentVerdict.FALLBACK_REQUIRED
        reason = "component_machine_screen_error:" + type(exc).__name__
    return MachineComponentScreen(
        verdict, reason, str(tool_label), total, discarded, candidate_count,
        transformed, commons, (time.perf_counter() - started) * 1000.0, hit,
    )


@dataclass
class ComponentShadowMetrics:
    """Read-only probe evidence, never a substitute for native D6 clearance."""
    probes: int = 0
    exact_tool_screens: int = 0
    aabb_rejected: int = 0
    exact_candidates: int = 0
    transformed_components: int = 0
    component_common_calls: int = 0
    measured_s: float = 0.0
    proven_component_hits: int = 0
    disagreements: int = 0
    incomplete_probes: int = 0
    examples: list = None

    def __post_init__(self):
        if self.examples is None:
            self.examples = []

    def to_dict(self):
        return {
            "scope": "COMPONENT_SHADOW_ADVISORY_ONLY",
            "probes": self.probes,
            "exact_tool_screens": self.exact_tool_screens,
            "aabb_rejected": self.aabb_rejected,
            "exact_candidates": self.exact_candidates,
            "transformed_components": self.transformed_components,
            "component_common_calls": self.component_common_calls,
            "measured_s": round(self.measured_s, 6),
            "proven_component_hits": self.proven_component_hits,
            "disagreements": self.disagreements,
            "incomplete_probes": self.incomplete_probes,
            "examples": self.examples,
            "qualification": "NOT_A_CLEARANCE_OR_CONTINUOUS_MOTION_CERTIFICATE",
        }


_shadow_metrics = ContextVar("smbs_component_shadow_metrics", default=None)


@contextmanager
def count_component_shadow_checks():
    """Use inside a single D6 benchmark scope; never writes user files."""
    stats = ComponentShadowMetrics()
    token = _shadow_metrics.set(stats)
    try:
        yield stats
    finally:
        _shadow_metrics.reset(token)


def record_component_shadow_probe(results, oracle_report, bend_id, phase, path_u):
    """Compare a screen with the authoritative full-joined legacy report."""
    stats = _shadow_metrics.get()
    if stats is None:
        return
    stats.probes += 1
    details = []
    from smbs_core.kinematics2d.native_collision_3d import CollisionClass
    for label, screen in results.items():
        stats.exact_tool_screens += 1
        stats.aabb_rejected += screen.aabb_rejected
        stats.exact_candidates += screen.candidate_count
        stats.transformed_components += screen.transformed_count
        stats.component_common_calls += screen.common_calls
        stats.measured_s += screen.elapsed_ms / 1000.0
        if screen.verdict == ComponentVerdict.PROVEN_PENETRATION:
            stats.proven_component_hits += 1
            observed = (
                oracle_report.punch_result if label == "PUNCH"
                else oracle_report.die_result
            )
            if observed.status != CollisionClass.PENETRATION:
                stats.disagreements += 1
                details.append({
                    "tool": label,
                    "disagreement": "component_positive_but_full_oracle_not_penetration",
                    "full_oracle_status": observed.status.value,
                    "component": screen.hit.component_id if screen.hit else None,
                })
        elif screen.reason.startswith(("component_machine_screen_error", "uncertain_exact_component", "candidate_budget")):
            stats.incomplete_probes += 1
        details.append({
            "tool": label,
            "verdict": screen.verdict.value,
            "reason": screen.reason,
            "candidates": screen.candidate_count,
            "common_calls": screen.common_calls,
            "hit": screen.hit.component_id if screen.hit else None,
        })
    if len(stats.examples) < 8:
        stats.examples.append({
            "bend_id": str(bend_id),
            "phase": str(phase),
            "path_u": round(float(path_u), 6),
            "results": details,
            "full_status": oracle_report.status.value,
        })
