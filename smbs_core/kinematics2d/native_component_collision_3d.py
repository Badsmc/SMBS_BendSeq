"""Opt-in exact OCC component-level collision REJECTION before sheet multiFuse.

This is not a replacement for full-sheet validation and never proves CLEAR.
The 21 authoritative panel/bend-zone Part.Shapes are built by the same
production builder. This module can only reject a single sampled pose after
one positive, finite, native OCC Common with a real selected punch or die.
UNKNOWN, invalid geometry, contacts, micro-overlaps, end-zone-only hits and
kernel failures always defer to the unchanged full joined-sheet oracle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Optional

from .native_collision_3d import (
    CollisionClass,
    PairClearanceResult,
    PunchCollisionZoneClassification,
    ToolCollisionReport,
    _evaluate_common_volume,
    check_solid_validity,
    classify_punch_overlap_zones,
    compute_aabb_gap,
)


@dataclass(frozen=True)
class ProvenComponentHit:
    component_id: str
    tool_label: str
    volume_mm3: float
    bbox_xyz_mm: Optional[dict]
    central_zone_proved: bool


@dataclass(frozen=True)
class ComponentScreenResult:
    hit: Optional[ProvenComponentHit]
    candidate_pairs: int
    aabb_rejected_pairs: int
    common_calls: int
    elapsed_ms: float
    fallback_reason: str

    @property
    def has_proven_rejection(self) -> bool:
        return self.hit is not None


def screen_native_sheet_components(
    components: Any,
    matrix: Any,
    punch_shape: Any,
    die_shape: Any,
    *,
    volume_tolerance_mm3: float,
    allow_end_zone_collision: bool,
    end_zone_length_mm: float,
) -> ComponentScreenResult:
    """Try only a *proven blocking penetration*; otherwise return fallback.

    Machine placement transforms independent copies, never source/cache shapes.
    Exact OCC Common is used only for components with overlapping AABBs.
    A positive component intersection proves a sheet/tool intersection when
    the authoritative panel/zone source model is valid. We do NOT sum small
    overlaps, infer CLEAR or resolve uncertain sheet integrity here.
    """
    started = perf_counter()
    candidates = rejects = commons = 0

    def result(hit=None, reason="needs_full_sheet_oracle"):
        return ComponentScreenResult(
            hit, candidates, rejects, commons,
            (perf_counter() - started) * 1000.0, reason,
        )

    try:
        if not math.isfinite(volume_tolerance_mm3) or volume_tolerance_mm3 < 0:
            return result(reason="invalid_volume_tolerance")
        if components is None or not components.labeled_shapes:
            return result(reason="no_authoritative_components")
        # Real tool validity is mandatory before issuing a proven rejection.
        for label, tool in (("PUNCH", punch_shape), ("DIE", die_shape)):
            valid, why = check_solid_validity(tool, label)
            if not valid:
                return result(reason="invalid_tool:" + why)

        # First validate ALL transformed components. This prevents accepting
        # a hit while another component is malformed or could not be placed.
        placed = []
        for component_id, original in components.labeled_shapes:
            machine_shape = original.copy()
            machine_shape.transformShape(matrix)
            valid, why = check_solid_validity(machine_shape, component_id)
            if not valid:
                return result(reason="invalid_component:" + why)
            placed.append((component_id, machine_shape))

        # DIE has no Conditional end-zone exemption. Prefer its blockers,
        # avoiding any ambiguous partial punch-end classification.
        for tool_label, tool in (("DIE", die_shape), ("PUNCH", punch_shape)):
            for component_id, sheet in placed:
                gap = compute_aabb_gap(sheet.BoundBox, tool.BoundBox)
                # Unknown bounds are NEVER sufficient to discard a pair.
                if gap is not None and gap > 1e-8:
                    rejects += 1
                    continue
                candidates += 1
                common_volume, bound, common_shape = _evaluate_common_volume(
                    sheet, tool, include_bbox=True, include_shape=True,
                )
                commons += 1
                if common_volume <= volume_tolerance_mm3:
                    continue

                central_proved = False
                if tool_label == "PUNCH" and allow_end_zone_collision:
                    # A partial component hit in an allowed end-zone is NOT
                    # blocking. Only significant central OCC evidence rejects.
                    zone = classify_punch_overlap_zones(
                        sheet, tool, end_zone_length_mm,
                        volume_tolerance_mm3=volume_tolerance_mm3,
                        _precomputed_common=common_shape,
                    )
                    central_proved = (
                        zone.classification in (
                            PunchCollisionZoneClassification.CENTRAL_ZONE,
                            PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE,
                        )
                        and zone.central_zone_overlap_volume_mm3
                        > volume_tolerance_mm3
                    )
                    if not central_proved:
                        continue

                return result(
                    ProvenComponentHit(
                        component_id=component_id,
                        tool_label=tool_label,
                        volume_mm3=common_volume,
                        bbox_xyz_mm=bound,
                        central_zone_proved=central_proved,
                    ),
                    reason="COMPONENT_PROVEN_PENETRATION",
                )
        return result()
    except Exception as exc:
        # Fail closed: never use a failed broadphase/Common as CLEAR/COLLISION.
        # The untouched whole-sheet path will decide or report UNVERIFIED.
        return result(reason="component_screen_exception:" +
                      type(exc).__name__ + ":" + str(exc))


def proven_component_report(screen: ComponentScreenResult) -> ToolCollisionReport:
    """Convey actual component-level penetration without inventing other evidence.

    The untested tool receives MISSING_EVIDENCE, not CLEAR. The aggregate
    is a proven blocking collision at this sampled pose, not whole-sheet
    topology or continuous path certification. Component volumes are LOWER
    BOUNDS, never claimed as the full-sheet penetration volume.
    """
    hit = screen.hit
    if hit is None:
        raise ValueError("component_screen_has_no_proven_hit")

    penetration = PairClearanceResult(
        CollisionClass.PENETRATION,
        "component_proven_penetration:" + hit.component_id,
        0.0, hit.volume_mm3, 0.0, screen.elapsed_ms,
        tool_label=hit.tool_label, overlap_bbox_xyz_mm=hit.bbox_xyz_mm,
        # The component proof uses actual Common, but is not a full-sheet
        # pair query; count actual calls in the screen's own counters.
        common_queries_count=screen.common_calls,
    )
    skipped = PairClearanceResult(
        CollisionClass.MISSING_EVIDENCE,
        "tool_not_evaluated_after_proven_component_collision",
        0.0, 0.0, None, 0.0,
    )
    punch = penetration if hit.tool_label == "PUNCH" else skipped
    die = penetration if hit.tool_label == "DIE" else skipped

    return ToolCollisionReport(
        status=CollisionClass.PENETRATION,
        punch_result=punch,
        die_result=die,
        is_safe=False,
        diagnostics=[
            "COMPONENT_PROVEN_PENETRATION:"
            + hit.tool_label + ":" + hit.component_id,
            "component_overlap_is_lower_bound_not_full_sheet_volume",
            "uninspected_tool_missing_evidence;not_continuously_certified",
        ],
        elapsed_ms=screen.elapsed_ms,
        is_conditional_safe=False,
        conditional_status="REJECTED_CENTRAL_OR_DIE_COLLISION",
    )
