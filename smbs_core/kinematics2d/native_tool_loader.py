"""native_tool_loader.py - Loads FreeCAD CAD tooling into Machine Coordinate Space.

Machine Coordinate Space (X_m, Y_m, Z_m) Contract:
- X_m: Along press brake tooling length (coaxial with active bend line).
       Midpoint of tooling length is placed at X_m = 0.0.
- Y_m: Horizontal transverse axis in cross-section plane (depth of bed, perpendicular to tooling).
       Die V-groove center is placed at Y_m = 0.0.
       Punch tip line is placed at Y_m = 0.0.
- Z_m: Vertical along ram motion:
       Die top shoulder reference plane is at Z_m = 0.0 (die body extends into -Z_m).
       Punch tip is at Z_m = ram_z_mm (punch body extends into +Z_m from the tip).

Deterministic, verified mappings from native CAD files & Tooling Library datums:
- `toollibrary/punch/puancon.FCStd` & `ToolLibrary25D`:
  Profile geometry extracted directly from CAD solids/sketches.
  Working tip point: X = -403.5635478801571, Y = -3834.154260829799.
  Tip radius: 0.8 mm, Included angle: 30.0 deg.
  Extrusion length: 100 mm along CAD Z.
  Machine Transform (det(R) = +1.0):
    X_m = Z_cad - 50.0
    Y_m = X_cad - (-403.5635478801571)
    Z_m = (Y_cad - (-3834.154260829799)) + ram_z_mm

- `toollibrary/die/matrica.FCStd` & `ToolLibrary25D`:
  Profile geometry extracted directly from CAD solids/sketches.
  V-groove center: X = -412.307718, top shoulder plane: Y = -3840.489599.
  V-opening: 32.0 mm, Shoulder radius: 2.0 mm, Die angle: 85.0 deg.
  Extrusion length: 100 mm along CAD Z.
  Machine Transform (det(R) = +1.0):
    X_m = Z_cad - 50.0
    Y_m = X_cad - (-412.307718)
    Z_m = Y_cad - (-3840.489599)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

try:
    import FreeCAD as App
    import Part
    HAS_FREECAD = True
except ImportError:
    App = None
    Part = None
    HAS_FREECAD = False

from smbs_core.kinematics2d.tooling_25d import ToolLibrary25D, ToolKind


@dataclass(frozen=True)
class ToolSectionConfig:
    section_id: str
    tool_id: str
    length_mm: float
    span_xm: Tuple[float, float]
    offset_xm: float


@dataclass(frozen=True)
class LoadedTool:
    tool_id: str
    kind: str  # "PUNCH" or "DIE"
    file_path: str
    cad_shape: Any  # Part.Shape
    machine_shape: Any  # Part.Shape in Machine Space
    tip_radius_mm: float
    included_angle_deg: float
    v_opening_mm: float = 0.0
    effective_v_opening_mm: float = 0.0
    shoulder_radius_mm: float = 0.0
    v_depth_mm: float = 0.0
    height_mm: float = 0.0
    length_mm: float = 100.0
    span_xm: Tuple[float, float] = (-50.0, 50.0)
    origin_cad: Tuple[float, float] = (0.0, 0.0)
    sections: Tuple[ToolSectionConfig, ...] = ()


@dataclass(frozen=True)
class ToolCoverageResult:
    is_covered: bool
    status: str
    bend_length_mm: float
    bend_span_xm: Tuple[float, float]
    tool_length_mm: float
    tool_span_xm: Tuple[float, float]
    uncovered_left_mm: float
    uncovered_right_mm: float
    gap_count: int
    max_gap_mm: float
    reason: str


def check_tool_coverage(
    bend_span_xm: Tuple[float, float],
    tool_span_xm: Tuple[float, float],
    sections: Optional[Sequence[ToolSectionConfig]] = None,
    tolerance_mm: float = 0.1,
) -> ToolCoverageResult:
    """Verify that physical tooling covers the entire span of the active bend line.

    Verifies:
    1. Overall coverage: tool span extends from bend start to bend end.
    2. Segment continuity: no gaps between consecutive tool sections.
    """
    b_min, b_max = min(bend_span_xm), max(bend_span_xm)
    t_min, t_max = min(tool_span_xm), max(tool_span_xm)

    bend_len = b_max - b_min
    tool_len = t_max - t_min

    uncovered_left = max(0.0, (t_min - b_min))
    uncovered_right = max(0.0, (b_max - t_max))

    # Check for gaps between sections if multiple sections are configured
    gap_count = 0
    max_gap = 0.0
    if sections and len(sections) > 1:
        sorted_secs = sorted(sections, key=lambda s: s.span_xm[0])
        for i in range(len(sorted_secs) - 1):
            curr_end = sorted_secs[i].span_xm[1]
            next_start = sorted_secs[i + 1].span_xm[0]
            gap = next_start - curr_end
            if gap > tolerance_mm:
                gap_count += 1
                max_gap = max(max_gap, gap)

    if gap_count > 0:
        reason = f"Tool assembly has {gap_count} gap(s) between sections (max gap: {max_gap:.2f}mm)."
        return ToolCoverageResult(
            is_covered=False,
            status="TOOL_ASSEMBLY_GAP",
            bend_length_mm=bend_len,
            bend_span_xm=(b_min, b_max),
            tool_length_mm=tool_len,
            tool_span_xm=(t_min, t_max),
            uncovered_left_mm=uncovered_left,
            uncovered_right_mm=uncovered_right,
            gap_count=gap_count,
            max_gap_mm=max_gap,
            reason=reason,
        )

    if uncovered_left > tolerance_mm or uncovered_right > tolerance_mm:
        reason = (
            f"Active bend length ({bend_len:.1f}mm, X_m [{b_min:.1f}, {b_max:.1f}]) "
            f"exceeds tooling span ({tool_len:.1f}mm, X_m [{t_min:.1f}, {t_max:.1f}]). "
            f"Uncovered: left={uncovered_left:.1f}mm, right={uncovered_right:.1f}mm."
        )
        return ToolCoverageResult(
            is_covered=False,
            status="INSUFFICIENT_TOOL_LENGTH",
            bend_length_mm=bend_len,
            bend_span_xm=(b_min, b_max),
            tool_length_mm=tool_len,
            tool_span_xm=(t_min, t_max),
            uncovered_left_mm=uncovered_left,
            uncovered_right_mm=uncovered_right,
            gap_count=0,
            max_gap_mm=0.0,
            reason=reason,
        )

    return ToolCoverageResult(
        is_covered=True,
        status="FULL_TOOL_COVERAGE",
        bend_length_mm=bend_len,
        bend_span_xm=(b_min, b_max),
        tool_length_mm=tool_len,
        tool_span_xm=(t_min, t_max),
        uncovered_left_mm=0.0,
        uncovered_right_mm=0.0,
        gap_count=0,
        max_gap_mm=0.0,
        reason="Tooling fully spans active bend line without gaps.",
    )


def _matrix_for_die(origin_cad: Tuple[float, float], length_mm: float) -> Any:
    """Returns FreeCAD 4x4 Placement Matrix for Die -> Machine Space."""
    if not HAS_FREECAD:
        return None
    v_center_x, top_y = origin_cad
    return App.Matrix(
        0.0, 0.0, 1.0, -0.5 * length_mm,
        1.0, 0.0, 0.0, -v_center_x,
        0.0, 1.0, 0.0, -top_y,
        0.0, 0.0, 0.0, 1.0,
    )


def _matrix_for_punch(origin_cad: Tuple[float, float], length_mm: float, ram_z_mm: float = 0.0) -> Any:
    """Returns FreeCAD 4x4 Placement Matrix for Punch -> Machine Space at ram_z_mm."""
    if not HAS_FREECAD:
        return None
    tip_x, tip_y = origin_cad
    return App.Matrix(
        0.0, 0.0, 1.0, -0.5 * length_mm,
        1.0, 0.0, 0.0, -tip_x,
        0.0, 1.0, 0.0, -tip_y + float(ram_z_mm),
        0.0, 0.0, 0.0, 1.0,
    )


def load_native_die(
    file_path: str | Path,
    library_root: Optional[str | Path] = None,
    section_offsets_xm: Optional[Sequence[float]] = None,
) -> LoadedTool:
    """Load native FreeCAD die CAD model and transform into Machine Space (Z_top = 0.0).

    If section_offsets_xm is provided (e.g. [-50.0, 50.0]), assembles multiple explicitly
    configured 100mm sections along the machine X_m axis.
    """
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Die CAD file not found: {p}")
    if not HAS_FREECAD:
        raise RuntimeError("FreeCAD runtime is required to load native CAD die")

    lib_dir = library_root or p.parents[1]
    tool_lib = ToolLibrary25D(lib_dir)
    profile = tool_lib.load_tool(p.stem, ToolKind.DIE)
    if profile.die_datums is None:
        raise ValueError(f"MISSING_TOOLING_EVIDENCE: Die '{p.stem}' has no verified die_datums.")

    datums = profile.die_datums
    origin_cad = (datums.v_groove_center[0], datums.top_datum_plane_y)
    sec_len = float(profile.length_mm) if profile.length_mm else 100.0

    # Preserve any pre-existing user-opened CAD tool documents.
    existing_documents = set(App.listDocuments())
    previous_document = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = App.openDocument(str(p), True)
    try:
        pad = doc.getObject("Pad") or doc.getObject("Body")
        if pad is None or not hasattr(pad, "Shape") or pad.Shape.isNull():
            for o in doc.Objects:
                if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids:
                    pad = o
                    break
        if pad is None or pad.Shape.isNull():
            raise ValueError(f"No valid Part.Shape solid found in {p}")

        cad_shape = pad.Shape.copy()
        base_mat = _matrix_for_die(origin_cad, sec_len)
        base_machine_shape = cad_shape.copy()
        base_machine_shape.transformShape(base_mat)

        if not base_machine_shape.isValid() or base_machine_shape.Volume <= 0:
            raise ValueError(f"Transformed die shape is invalid or non-solid: {p}")

        # Multiple sections assembly if offsets provided
        if section_offsets_xm and len(section_offsets_xm) > 1:
            sections_list = []
            fused_shape = None
            for idx, off_x in enumerate(section_offsets_xm):
                sec_shape = base_machine_shape.copy()
                sec_mat = App.Matrix(1, 0, 0, off_x, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                sec_shape.transformShape(sec_mat)
                fused_shape = sec_shape if fused_shape is None else fused_shape.fuse(sec_shape)
                sec_span = (off_x - 0.5 * sec_len, off_x + 0.5 * sec_len)
                sections_list.append(ToolSectionConfig(
                    section_id=f"{p.stem}_{idx+1}",
                    tool_id=p.stem,
                    length_mm=sec_len,
                    span_xm=sec_span,
                    offset_xm=off_x,
                ))
            total_len = sec_len * len(section_offsets_xm)
            span_xm = (min(s.span_xm[0] for s in sections_list), max(s.span_xm[1] for s in sections_list))
            machine_shape = fused_shape
            sections = tuple(sections_list)
        else:
            total_len = sec_len
            span_xm = (-0.5 * sec_len, 0.5 * sec_len)
            machine_shape = base_machine_shape
            sections = (ToolSectionConfig(
                section_id=f"{p.stem}_1",
                tool_id=p.stem,
                length_mm=sec_len,
                span_xm=span_xm,
                offset_xm=0.0,
            ),)

        # Authoritative shoulder fillet center detection from machine solid:
        # The true kinematic die opening V_effective is 2 * y_shoulder_fillet_center
        # which governs the exact tangent contact with the sheet workpiece.
        effective_v = float(profile.v_opening_mm)
        try:
            shoulder_centers = []
            sh_r = float(profile.shoulder_radius_mm)
            for f in machine_shape.Faces:
                surf = getattr(f, "Surface", None)
                if surf is not None and hasattr(surf, "Radius") and abs(surf.Radius - sh_r) < 1e-3:
                    if abs(f.BoundBox.ZMax - 0.0) < 1e-3:
                        shoulder_centers.append(surf.Center)
            if shoulder_centers:
                y_offs = [abs(c.y) for c in shoulder_centers]
                effective_v = float(2.0 * (sum(y_offs) / len(y_offs)))
        except Exception:
            pass

        return LoadedTool(
            tool_id=p.stem,
            kind="DIE",
            file_path=str(p),
            cad_shape=cad_shape,
            machine_shape=machine_shape,
            tip_radius_mm=0.0,
            included_angle_deg=float(profile.die_angle_deg),
            v_opening_mm=float(profile.v_opening_mm),
            effective_v_opening_mm=effective_v,
            shoulder_radius_mm=float(profile.shoulder_radius_mm),
            v_depth_mm=float(datums.v_depth_mm),
            height_mm=float(profile.height_mm),
            length_mm=total_len,
            span_xm=span_xm,
            origin_cad=origin_cad,
            sections=sections,
        )
    finally:
        if doc.Name not in existing_documents:
            App.closeDocument(doc.Name)
        if previous_document is not None and App.getDocument(previous_document) is not None:
            App.setActiveDocument(previous_document)


def load_native_punch(
    file_path: str | Path,
    ram_z_mm: float = 0.0,
    library_root: Optional[str | Path] = None,
    section_offsets_xm: Optional[Sequence[float]] = None,
) -> LoadedTool:
    """Load native FreeCAD punch CAD model and transform into Machine Space (tip at ram_z_mm).

    If section_offsets_xm is provided (e.g. [-50.0, 50.0]), assembles multiple explicitly
    configured 100mm sections along the machine X_m axis.
    """
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Punch CAD file not found: {p}")
    if not HAS_FREECAD:
        raise RuntimeError("FreeCAD runtime is required to load native CAD punch")

    lib_dir = library_root or p.parents[1]
    tool_lib = ToolLibrary25D(lib_dir)
    profile = tool_lib.load_tool(p.stem, ToolKind.PUNCH)
    if profile.punch_datums is None:
        raise ValueError(f"MISSING_TOOLING_EVIDENCE: Punch '{p.stem}' has no verified punch_datums.")

    datums = profile.punch_datums
    origin_cad = (datums.working_tip[0], datums.working_tip[1])
    sec_len = float(profile.length_mm) if profile.length_mm else 100.0

    # Preserve any pre-existing user-opened CAD tool documents.
    existing_documents = set(App.listDocuments())
    previous_document = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = App.openDocument(str(p), True)
    try:
        pad = doc.getObject("Pad") or doc.getObject("Body")
        if pad is None or not hasattr(pad, "Shape") or pad.Shape.isNull():
            for o in doc.Objects:
                if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids:
                    pad = o
                    break
        if pad is None or pad.Shape.isNull():
            raise ValueError(f"No valid Part.Shape solid found in {p}")

        cad_shape = pad.Shape.copy()
        base_mat = _matrix_for_punch(origin_cad, sec_len, ram_z_mm)
        base_machine_shape = cad_shape.copy()
        base_machine_shape.transformShape(base_mat)

        if not base_machine_shape.isValid() or base_machine_shape.Volume <= 0:
            raise ValueError(f"Transformed punch shape is invalid or non-solid: {p}")

        # Multiple sections assembly if offsets provided
        if section_offsets_xm and len(section_offsets_xm) > 1:
            sections_list = []
            fused_shape = None
            for idx, off_x in enumerate(section_offsets_xm):
                sec_shape = base_machine_shape.copy()
                sec_mat = App.Matrix(1, 0, 0, off_x, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                sec_shape.transformShape(sec_mat)
                fused_shape = sec_shape if fused_shape is None else fused_shape.fuse(sec_shape)
                sec_span = (off_x - 0.5 * sec_len, off_x + 0.5 * sec_len)
                sections_list.append(ToolSectionConfig(
                    section_id=f"{p.stem}_{idx+1}",
                    tool_id=p.stem,
                    length_mm=sec_len,
                    span_xm=sec_span,
                    offset_xm=off_x,
                ))
            total_len = sec_len * len(section_offsets_xm)
            span_xm = (min(s.span_xm[0] for s in sections_list), max(s.span_xm[1] for s in sections_list))
            machine_shape = fused_shape
            sections = tuple(sections_list)
        else:
            total_len = sec_len
            span_xm = (-0.5 * sec_len, 0.5 * sec_len)
            machine_shape = base_machine_shape
            sections = (ToolSectionConfig(
                section_id=f"{p.stem}_1",
                tool_id=p.stem,
                length_mm=sec_len,
                span_xm=span_xm,
                offset_xm=0.0,
            ),)

        return LoadedTool(
            tool_id=p.stem,
            kind="PUNCH",
            file_path=str(p),
            cad_shape=cad_shape,
            machine_shape=machine_shape,
            tip_radius_mm=float(profile.tip_radius_mm),
            included_angle_deg=float(datums.included_angle_deg),
            v_opening_mm=0.0,
            shoulder_radius_mm=0.0,
            v_depth_mm=0.0,
            height_mm=float(profile.height_mm),
            length_mm=total_len,
            span_xm=span_xm,
            origin_cad=origin_cad,
            sections=sections,
        )
    finally:
        if doc.Name not in existing_documents:
            App.closeDocument(doc.Name)
        if previous_document is not None and App.getDocument(previous_document) is not None:
            App.setActiveDocument(previous_document)


def set_punch_ram_z(punch: LoadedTool, ram_z_mm: float) -> LoadedTool:
    """Create a new LoadedTool punch instance with updated ram_z_mm coordinate."""
    if punch.kind != "PUNCH":
        raise ValueError("Can only set ram_z for PUNCH tool")

    sec_len = punch.sections[0].length_mm if punch.sections else 100.0
    base_mat = _matrix_for_punch(punch.origin_cad, sec_len, ram_z_mm)
    base_machine_shape = punch.cad_shape.copy()
    base_machine_shape.transformShape(base_mat)

    if punch.sections and len(punch.sections) > 1:
        fused_shape = None
        for sec in punch.sections:
            sec_shape = base_machine_shape.copy()
            sec_mat = App.Matrix(1, 0, 0, sec.offset_xm, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
            sec_shape.transformShape(sec_mat)
            fused_shape = sec_shape if fused_shape is None else fused_shape.fuse(sec_shape)
        machine_shape = fused_shape
    else:
        machine_shape = base_machine_shape

    return LoadedTool(
        tool_id=punch.tool_id,
        kind=punch.kind,
        file_path=punch.file_path,
        cad_shape=punch.cad_shape,
        machine_shape=machine_shape,
        tip_radius_mm=punch.tip_radius_mm,
        included_angle_deg=punch.included_angle_deg,
        v_opening_mm=punch.v_opening_mm,
        shoulder_radius_mm=punch.shoulder_radius_mm,
        v_depth_mm=punch.v_depth_mm,
        height_mm=punch.height_mm,
        length_mm=punch.length_mm,
        span_xm=punch.span_xm,
        origin_cad=punch.origin_cad,
        sections=punch.sections,
    )
