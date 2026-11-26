"""Read-only multi-station 2.5D contact preview for one physical machine pose.

This is a RISK SCREEN, never a proof of free trajectory or blocking collision.
All stations reuse one FoldState, one machine placement, one aligned tool setup.
Tool polygons must be verified in longitudinal span before they can contribute
station-specific evidence; virtual extrusions are not mistaken for physical CAD.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Optional

from .contact_oracle_2d import ContactStatus, check_sheet_tooling_preview
from .machine_section_3d import compute_machine_cutting_plane_section


@dataclass(frozen=True)
class StationFinding:
    x_machine_mm: float
    status: str
    reason: str
    findings: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_machine_mm": self.x_machine_mm,
            "status": self.status,
            "reason": self.reason,
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class MultiStationPreview:
    status: str
    stations: tuple[StationFinding, ...]
    evidence: str = "SAMPLED_2D_ONLY_NOT_3D_PROOF"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence": self.evidence,
            "stations": [station.to_dict() for station in self.stations],
        }


def _within_span(x: float, span: tuple[float, float]) -> bool:
    low, high = float(span[0]), float(span[1])
    if not all(isfinite(v) for v in (low, high)) or low > high:
        raise ValueError("invalid verified longitudinal tooling span")
    return low <= x <= high


def screen_machine_stations_2d(
    fold_state: Any,
    panel_vertices_xy: dict,
    placement: Any,
    active_bend_id: str,
    tooling: Any,
    stations_xm: Iterable[float],
    *,
    verified_punch_span_xm: Optional[tuple[float, float]] = None,
    verified_die_span_xm: Optional[tuple[float, float]] = None,
    tolerance_mm: float = 0.02,
    segments: int = 32,
) -> MultiStationPreview:
    """Sample workpiece/tool overlap at longitudinal machine X positions.

    A station outside either verified tool span is UNVERIFIED here: the
    transverse tooling profile alone is insufficient to model end-cap contacts.
    Missing longitudinal CAD spans are likewise unverified, even if a 2D
    preview looks clear. No collision result is used to prune a candidate.
    """
    xs = tuple(float(x) for x in stations_xm)
    if not xs or not all(isfinite(x) for x in xs):
        raise ValueError("stations_xm must contain finite positions")
    if tolerance_mm <= 0 or not isfinite(tolerance_mm):
        raise ValueError("tolerance_mm must be positive and finite")
    if segments < 4:
        raise ValueError("segments must be at least 4")
    if verified_punch_span_xm is not None:
        _within_span(xs[0], verified_punch_span_xm)
    if verified_die_span_xm is not None:
        _within_span(xs[0], verified_die_span_xm)
    results: list[StationFinding] = []
    for x in xs:
        if verified_punch_span_xm is None or verified_die_span_xm is None:
            results.append(StationFinding(x, "UNVERIFIED",
                                          "verified_tool_longitudinal_spans_missing", ()))
            continue
        if not _within_span(x, verified_punch_span_xm) or not _within_span(x, verified_die_span_xm):
            results.append(StationFinding(x, "UNVERIFIED",
                                          "outside_tool_span_or_end_zone_unmodelled", ()))
            continue
        try:
            section = compute_machine_cutting_plane_section(
                fold_state, panel_vertices_xy, placement,
                active_bend_id=active_bend_id, segments=segments, target_xm=x,
            )
            report = check_sheet_tooling_preview(section, tooling, tolerance_mm=tolerance_mm)
            findings = tuple({
                "tool": f.tool,
                "sheet_component": f.sheet_component,
                "status": f.status.value,
                "reason": f.reason,
            } for f in report.findings if f.status is not ContactStatus.CLEAR)
            results.append(StationFinding(x, report.status.value,
                                          "sampled_2d_section_not_3d_validated", findings))
        except Exception as exc:
            results.append(StationFinding(x, "UNVERIFIED",
                                          f"section_or_contact_error:{type(exc).__name__}:{exc}", ()))
    # A sampled overlap is a warning, not a proven machine collision.
    if any(r.status == "INTERFERENCE" for r in results):
        status = "2D_INTERFERENCE_REQUIRES_3D"
    elif any(r.status == "UNVERIFIED" for r in results):
        status = "UNVERIFIED_NEEDS_3D"
    else:
        status = "SAMPLED_SECTIONS_CLEAR_OR_TOUCH_3D_REQUIRED"
    return MultiStationPreview(status, tuple(results))
