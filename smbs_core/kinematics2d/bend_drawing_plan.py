"""Canonical, immutable SheetMetal bend numbering plan for TechDraw.

No FreeCAD or GUI dependency. Does not generate TechDraw pages or validate
physical feasibility: it only converts an already accepted planner result and
SheetMetal's existing bend/sketch geometry into traceable print instructions.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from typing import Any, Sequence

from smbs_core.kinematics2d.axis_matching import BendMatchError, match_bend_axes


class BendDrawingPlanError(ValueError):
    """Unable to produce an unambiguous and up-to-date numbered bend plan."""


@dataclass(frozen=True)
class NumberedBend:
    operation_number: int
    bend_id: str
    sketch_index: int
    line_xy: tuple[tuple[float, float], tuple[float, float]]
    signed_angle_deg: float
    bend_length_mm: float
    axis_offset_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_number": self.operation_number,
            "bend_id": self.bend_id,
            "sketch_index": self.sketch_index,
            "line_xy": [list(pt) for pt in self.line_xy],
            "signed_angle_deg": self.signed_angle_deg,
            "bend_length_mm": self.bend_length_mm,
            "axis_offset_mm": self.axis_offset_mm,
        }


@dataclass(frozen=True)
class BendDrawingPlan:
    schema_version: int
    source_document: str
    unfold_object: str
    source_object: str
    input_signature_sha256: str
    accepted_status: str
    operations: tuple[NumberedBend, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_document": self.source_document,
            "unfold_object": self.unfold_object,
            "source_object": self.source_object,
            "input_signature_sha256": self.input_signature_sha256,
            "accepted_status": self.accepted_status,
            "operations": [b.to_dict() for b in self.operations],
        }


def _xy_line(line: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    if len(line) != 2 or any(len(point) != 2 for point in line):
        raise BendDrawingPlanError("Each bend sketch line requires two XY points")
    points = tuple(tuple(float(x) for x in p) for p in line)
    if not all(isfinite(x) for point in points for x in point):
        raise BendDrawingPlanError("Non-finite bend sketch coordinates")
    if hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]) <= 0:
        raise BendDrawingPlanError("Zero-length bend sketch line")
    return points


def _source_snapshot(unfold: Any, sequence: tuple[str, ...], status: str, document: str) -> dict:
    return {
        "source_document": document,
        "unfold_object": str(unfold.unfold_object),
        "source_object": str(unfold.source_object),
        "source_face": str(unfold.source_face),
        "k_factor_standard": str(unfold.k_factor_standard),
        "thickness_mm": float(unfold.thickness_mm),
        "bends": [
            {
                "id": str(b.bend_id),
                "angle": float(b.fold_angle_deg),
                "source_angle": float(b.source_angle_deg),
                "length": float(b.length_mm),
                "start": [float(x) for x in b.start_xyz],
                "end": [float(x) for x in b.end_xyz],
                "radius": float(b.radius_mm),
                "allowance": float(b.bend_allowance_mm),
                "thickness": float(b.thickness_mm),
                "k_factor": float(b.k_factor_ansi),
            }
            for b in unfold.bends
        ],
        "sketch_lines": [
            [list(p) for p in _xy_line(line)]
            for line in unfold.sketch_bend_lines_xy
        ],
        "sequence": list(sequence),
        "accepted_status": status,
    }


def _signature(snapshot: dict) -> str:
    return sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def create_bend_drawing_plan(
    unfold: Any,
    accepted_sequence: Sequence[str],
    *,
    source_document: str,
    accepted_status: str,
    verified: bool,
    axis_tolerance_mm: float = 0.01,
) -> BendDrawingPlan:
    """Map an accepted sequence to unique original SheetMetal sketch lines.

    Refuses incomplete/ambiguous ordering and geometrical mismatch. Signs are
    SheetMetal fold angles, never guesses based on bend ID or line direction.
    """
    if not verified or accepted_status not in ("STRICT_VALIDATED", "CONDITIONAL_VALIDATED"):
        raise BendDrawingPlanError("Accepted planner verification is required")
    if not source_document or not str(source_document).strip():
        raise BendDrawingPlanError("Source document identity is required")
    sequence = tuple(str(x) for x in accepted_sequence)
    bends = tuple(unfold.bends)
    if not bends or len(sequence) != len(bends):
        raise BendDrawingPlanError("Exactly one ordered operation per bend is required")
    if any(not x for x in sequence) or len(set(sequence)) != len(sequence):
        raise BendDrawingPlanError("Duplicate or empty bend ID in accepted sequence")
    ids = tuple(str(b.bend_id) for b in bends)
    if len(set(ids)) != len(ids) or set(ids) != set(sequence):
        raise BendDrawingPlanError("Accepted sequence differs from SheetMetal bend IDs")
    lines = tuple(_xy_line(x) for x in unfold.sketch_bend_lines_xy)
    axes = tuple((
        tuple(float(v) for v in bend.start_xyz[:2]),
        tuple(float(v) for v in bend.end_xyz[:2]),
    ) for bend in bends)
    try:
        matches = match_bend_axes(axes, lines, axis_tolerance_mm=axis_tolerance_mm)
    except (BendMatchError, TypeError, ValueError) as exc:
        raise BendDrawingPlanError("Bend-to-sketch mapping failed: " + str(exc)) from exc
    by_id = {b.bend_id: (b, m) for b, m in zip(bends, matches)}
    operations = []
    for number, bend_id in enumerate(sequence, 1):
        bend, match = by_id[bend_id]
        angle = float(bend.fold_angle_deg)
        length = float(bend.length_mm)
        if not isfinite(angle) or not isfinite(length) or length <= 0:
            raise BendDrawingPlanError(f"Invalid bend angle or length: {bend_id}")
        operations.append(NumberedBend(
            operation_number=number,
            bend_id=bend_id,
            sketch_index=match.sketch_index,
            line_xy=lines[match.sketch_index],
            signed_angle_deg=angle,
            bend_length_mm=length,
            axis_offset_mm=match.offset_mm,
        ))
    snapshot = _source_snapshot(unfold, sequence, accepted_status, source_document)
    return BendDrawingPlan(
        schema_version=1,
        source_document=str(source_document),
        unfold_object=str(unfold.unfold_object),
        source_object=str(unfold.source_object),
        input_signature_sha256=_signature(snapshot),
        accepted_status=accepted_status,
        operations=tuple(operations),
    )


def is_bend_drawing_plan_current(
    plan: BendDrawingPlan,
    unfold: Any,
    accepted_sequence: Sequence[str],
    *,
    source_document: str,
    accepted_status: str,
) -> bool:
    """Detect changes in sequence or canonical SheetMetal geometry metadata."""
    if plan.schema_version != 1:
        return False
    try:
        snapshot = _source_snapshot(unfold, tuple(str(x) for x in accepted_sequence),
                                    accepted_status, source_document)
        return plan.input_signature_sha256 == _signature(snapshot)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
