"""
flat_annotations.py - Bend sequence annotations on flat pattern (S.18a, requirement 1).

Pure Python, no FreeCAD, no OCC. Reads 04/07/09 via RunArtifacts.
Produces list of BendAnnotation with midpoints on the flat pattern.

Strict contract: bend_line_flat_pattern is mandatory. axis_point is NOT used as fallback
(it lives in the folded-model coordinate system, not on the flat pattern — see KL-13).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from smbs_core.viewer.graph_builder import ViewerGraphs
from smbs_core.viewer.run_loader import RunArtifacts


VERIFICATION_VERIFIED = "VERIFIED"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
VERIFICATION_UNVERIFIED = "UNVERIFIED"


class FlatAnnotationsError(Exception):
    """Raised when annotations cannot be computed (missing fields, unknown bends, corruption)."""


@dataclass(frozen=True)
class BendAnnotation:
    step: int
    bend_id: str
    midpoint: Tuple[float, float, float]
    ui_direction: str
    verification: str


def _extract_bend_line(bend_payload: Dict[str, Any], bend_id: str) -> Tuple[float, float, float]:
    """
    Extract midpoint of the bend line on the flat pattern.

    Raises FlatAnnotationsError if bend_line_flat_pattern or start/end coordinates are missing or invalid.
    """
    bl = bend_payload.get("bend_line_flat_pattern")
    if bl is None:
        raise FlatAnnotationsError(
            f"Bend '{bend_id}' has no bend_line_flat_pattern in 04_canonical_part.json. "
            f"This run was generated before bend_line_flat_pattern was added to the pipeline "
            f"(pre-2026-10-17). axis_point is NOT used as fallback: it lives in the folded-model "
            f"coordinate system, not on the flat pattern. Regenerate the run with the current "
            f"pipeline (`smbs-cli part.FCStd`) to enable annotations."
        )
    try:
        start = bl["start_mm"]
        end = bl["end_mm"]
    except KeyError as err:
        raise FlatAnnotationsError(
            f"Bend '{bend_id}' bend_line_flat_pattern missing required field: {err}"
        ) from err

    if not isinstance(start, (list, tuple)) or len(start) != 3 or \
       not isinstance(end, (list, tuple)) or len(end) != 3:
        raise FlatAnnotationsError(
            f"Bend '{bend_id}' bend_line_flat_pattern start/end must be 3-element vectors, "
            f"got start={start}, end={end}"
        )
    return (
        (start[0] + end[0]) / 2.0,
        (start[1] + end[1]) / 2.0,
        (start[2] + end[2]) / 2.0,
    )


def compute_annotations(
    artifacts: RunArtifacts,
    graphs: ViewerGraphs,
) -> List[BendAnnotation]:
    """
    Compute bend-sequence annotations for the flat pattern.

    Raises FlatAnnotationsError on:
      - candidate_sequence[i] missing "step" or "bend_id"
      - bend_id from sequence not in 04 payload.bends
      - bend in sequence missing "bend_line_flat_pattern" (actionable message)
      - bend_line_flat_pattern missing "start_mm" or "end_mm"
      - bend_line_flat_pattern start/end not 3-element vectors
      - duplicate step value across candidate_sequence
      - 09 present but missing payload.verification_status

    Empty candidate_sequence is VALID -> returns [].

    Absence of 09 -> all annotations VERIFICATION_UNVERIFIED (invariant #1: UNKNOWN != VALID).
    """
    try:
        candidate_sequence = artifacts.search_result["payload"]["candidate_sequence"]
    except (KeyError, TypeError) as err:
        raise FlatAnnotationsError(f"Missing 'candidate_sequence' in search_result payload: {err}") from err

    if not candidate_sequence:
        # Empty sequence -> no annotations needed. Do not validate 09 or canonical_part bends:
        # both are only consumed when there is at least one bend to annotate. This preserves
        # a valid "nothing to annotate" path even if 09 is malformed (e.g. an old partial run).
        return []

    # Verification status extraction
    vr = artifacts.verification_result
    if vr is None:
        verification = VERIFICATION_UNVERIFIED
    else:
        try:
            verification = vr["payload"]["verification_status"]
        except (KeyError, TypeError) as err:
            raise FlatAnnotationsError(
                f"09_verification_result present but payload.verification_status missing: {err}"
            ) from err

    try:
        canonical_bends = artifacts.canonical_part["payload"]["bends"]
    except (KeyError, TypeError) as err:
        raise FlatAnnotationsError(f"Missing 'bends' in canonical_part payload: {err}") from err

    # Duplicate step check and validation
    steps_seen = set()
    duplicates = set()
    for item in candidate_sequence:
        if not isinstance(item, dict):
            raise FlatAnnotationsError(f"candidate_sequence item must be a dict, got {type(item)}")
        try:
            step = item["step"]
        except KeyError as err:
            raise FlatAnnotationsError(f"candidate_sequence item missing required field: {err}") from err
        if not isinstance(step, int) or isinstance(step, bool):
            raise FlatAnnotationsError(f"candidate_sequence 'step' must be an int, got {step!r}")

        if step in steps_seen:
            duplicates.add(step)
        steps_seen.add(step)

    if duplicates:
        raise FlatAnnotationsError(f"Duplicate step value across candidate_sequence: {sorted(duplicates)}")

    annotations: List[BendAnnotation] = []
    for item in candidate_sequence:
        try:
            bend_id = item["bend_id"]
        except KeyError as err:
            raise FlatAnnotationsError(f"candidate_sequence item missing required field: {err}") from err

        if not bend_id:
            raise FlatAnnotationsError("candidate_sequence item has empty 'bend_id'")

        if bend_id not in canonical_bends:
            raise FlatAnnotationsError(f"bend_id '{bend_id}' from candidate_sequence not in 04_canonical_part bends")

        bend_payload = canonical_bends[bend_id]
        midpoint = _extract_bend_line(bend_payload, bend_id)

        try:
            ui_direction = graphs.direction_by_bend[bend_id]
        except KeyError:
            raise FlatAnnotationsError(f"bend_id '{bend_id}' not found in graphs.direction_by_bend")

        annotations.append(
            BendAnnotation(
                step=item["step"],
                bend_id=bend_id,
                midpoint=midpoint,
                ui_direction=ui_direction,
                verification=verification,
            )
        )

    return sorted(annotations, key=lambda a: a.step)
