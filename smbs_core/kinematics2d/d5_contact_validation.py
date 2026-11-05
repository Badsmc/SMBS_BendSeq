"""D5.1 read-only punch/die material-interference gate for a machine section.

This is a conservative cross-section *preview*, not full 3D certification.
Uses the authoritative machine-space section and imported tooling profiles,
delegating polygon contact classification to the existing contact oracle.

No mutation of FoldState, tooling or cycle/controller is permitted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any, Mapping

from .contact_oracle_2d import (
    ContactGeometryError, ContactStatus, classify_polygon_contact,
)


class D51Status(str, Enum):
    CLEAR = "CLEAR"
    CONTACT = "CONTACT"
    COLLISION = "COLLISION"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class D51Finding:
    tool: str
    component: str
    status: D51Status
    reason: str


@dataclass(frozen=True)
class D51Report:
    status: D51Status
    bend_id: str
    phase: str
    findings: tuple[D51Finding, ...]
    tolerance_mm: float
    mode: str = "2D_SECTION_PREVIEW_NOT_3D_CERTIFIED"

    def as_trace(self) -> dict:
        return {
            "status": self.status.value,
            "bend_id": self.bend_id,
            "phase": self.phase,
            "tolerance_mm": self.tolerance_mm,
            "mode": self.mode,
            "collision_validated_3d": False,
            "findings": [
                {"tool": f.tool, "component": f.component,
                 "status": f.status.value, "reason": f.reason}
                for f in self.findings
            ],
        }


def _unverified(bend_id: str, phase: str, reason: str, tol: float) -> D51Report:
    return D51Report(
        D51Status.UNVERIFIED, bend_id, phase,
        (D51Finding("MACHINE", "*", D51Status.UNVERIFIED, reason),), tol,
    )


def check_d51_machine_section(
    section: Mapping[str, Any] | None,
    tooling: Any,
    *,
    bend_id: str,
    phase: str,
    tolerance_mm: float = 0.02,
    arc_segments: int = 32,
) -> D51Report:
    """Check finite-thickness section components against actual tooling material.

    Both real imported profiles must be verified and aligned. Any missing or
    degenerate required geometry yields UNVERIFIED. CONTACT is boundary-only
    (not an endorsement of mechanical support). Positive-area overlap is
    COLLISION. The section is assumed already placed in machine (s,z) space.
    """
    tol = float(tolerance_mm)
    if not isfinite(tol) or not 1e-5 <= tol <= 1.0:
        raise ValueError("tolerance_mm must be within [1e-5, 1.0]")
    if not isinstance(section, Mapping):
        return _unverified(bend_id, phase, "missing_machine_section", tol)
    if not tooling or not getattr(tooling, "aligned", False):
        return _unverified(bend_id, phase, "tooling_not_aligned", tol)
    if not bend_id or str(section.get("active_bend_id", bend_id)) != str(bend_id):
        return _unverified(bend_id, phase, "section_bend_id_mismatch", tol)
    if "cutting_plane_xm" not in section or abs(float(section["cutting_plane_xm"])) > 1e-6:
        return _unverified(bend_id, phase, "unverified_section_plane", tol)

    # Fail closed: a tool with an inferred/absent datum is not verified geometry.
    for name, tool in (("PUNCH", getattr(tooling, "punch", None)),
                       ("DIE", getattr(tooling, "die", None))):
        status = getattr(getattr(tool, "validation_status", None), "value", None)
        if status != "GEOMETRY_VERIFIED":
            return _unverified(bend_id, phase, name.lower() + "_profile_not_verified", tol)
    if getattr(tooling, "aligned_bend_id", str(bend_id)) not in (None, str(bend_id)):
        return _unverified(bend_id, phase, "tooling_aligned_to_other_bend", tol)

    components = []
    for key in ("fixed_polygon", "moving_polygon", "zone_polygon"):
        poly = section.get(key)
        if poly:
            components.append((key, poly))
    for key in ("other_panel_polygons", "other_zone_polygons"):
        for name, poly in section.get(key, ()):
            components.append((str(name), poly))

    if not components:
        return _unverified(bend_id, phase, "missing_finite_thickness_sheet_sections", tol)

    findings: list[D51Finding] = []
    for tool_name, getter_name in (
        ("PUNCH", "punch_polygons_machine"),
        ("DIE", "die_polygons_machine"),
    ):
        try:
            getter = getattr(tooling, getter_name)
            shape = getter(arc_segments=arc_segments)
            if not shape or len(shape) != 2:
                raise ContactGeometryError("missing_tool_polygon")
            outer, holes = shape
            if not outer:
                raise ContactGeometryError("empty_tool_polygon")
        except (AttributeError, TypeError, ValueError, ContactGeometryError) as exc:
            findings.append(D51Finding(tool_name, "*", D51Status.UNVERIFIED,
                                       "tool_profile_unavailable: " + str(exc)))
            continue

        for component_id, poly in components:
            try:
                status, reason = classify_polygon_contact(poly, outer, holes, tol)
                mapped = {
                    ContactStatus.CLEAR: D51Status.CLEAR,
                    ContactStatus.TOUCH: D51Status.CONTACT,
                    ContactStatus.INTERFERENCE: D51Status.COLLISION,
                    ContactStatus.UNVERIFIED: D51Status.UNVERIFIED,
                }[status]
            except (ContactGeometryError, TypeError, ValueError, OverflowError) as exc:
                mapped = D51Status.UNVERIFIED
                reason = "invalid_component_or_tool_geometry: " + str(exc)
            findings.append(D51Finding(tool_name, component_id, mapped, reason))

    # A collision remains visible even if other components could not be checked.
    for status in (D51Status.COLLISION, D51Status.UNVERIFIED,
                   D51Status.CONTACT, D51Status.CLEAR):
        if any(f.status == status for f in findings):
            return D51Report(status, str(bend_id), str(phase), tuple(findings), tol)
    return _unverified(bend_id, phase, "no_pairs_checked", tol)
