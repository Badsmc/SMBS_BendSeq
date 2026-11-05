"""native_tool_loader.py - Loads FreeCAD CAD tooling into Machine Coordinate Space.

Machine Coordinate Space (X_m, Y_m, Z_m) Contract:
- X_m: Along press brake tooling length (coaxial with active bend line).
       Midpoint of 100 mm tooling length is placed at X_m = 0.0 (X_m in [-50, +50]).
- Y_m: Horizontal transverse axis in cross-section plane (depth of bed, perpendicular to tooling).
       Die V-groove center is placed at Y_m = 0.0.
       Punch tip line is placed at Y_m = 0.0.
- Z_m: Vertical along ram motion:
       Die top shoulder reference plane is at Z_m = 0.0 (die body extends into -Z_m, [-60, 0]).
       Punch tip is at Z_m = ram_z_mm (punch body extends into +Z_m from the tip).

Deterministic, verified mappings from native CAD files:
- `toollibrary/punch/puancon.FCStd`:
  CAD Pad BoundBox: X in [-423.564, -397.564], Y in [-3834.154, -3700.154], Z in [0, 100].
  Working tip point: X = -403.5635478801571, Y = -3834.154260829799.
  Extrusion length: 100 mm along CAD Z.
  Machine Transform (det(R) = +1.0):
    X_m = Z_cad - 50.0
    Y_m = X_cad - (-403.5635478801571)
    Z_m = (Y_cad - (-3834.154260829799)) + ram_z_mm

- `toollibrary/die/matrica.FCStd`:
  CAD Pad BoundBox: X in [-442.308, -382.308], Y in [-3900.490, -3840.490], Z in [0, 100].
  V-groove center: X = -412.307718, top shoulder plane: Y = -3840.489599.
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
from typing import Any, Optional, Tuple

try:
    import FreeCAD as App
    import Part
    HAS_FREECAD = True
except ImportError:
    App = None
    Part = None
    HAS_FREECAD = False


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
    shoulder_radius_mm: float = 0.0
    height_mm: float = 0.0
    length_mm: float = 100.0


def _matrix_for_die() -> Any:
    """Returns FreeCAD 4x4 Placement Matrix for matrica.FCStd -> Machine Space."""
    if not HAS_FREECAD:
        return None
    # X_m = Z_cad - 50.0
    # Y_m = X_cad + 412.307718
    # Z_m = Y_cad + 3840.489599
    return App.Matrix(
        0.0, 0.0, 1.0, -50.0,
        1.0, 0.0, 0.0, 412.307718,
        0.0, 1.0, 0.0, 3840.489599,
        0.0, 0.0, 0.0, 1.0,
    )


def _matrix_for_punch(ram_z_mm: float = 0.0) -> Any:
    """Returns FreeCAD 4x4 Placement Matrix for puancon.FCStd -> Machine Space at ram_z_mm."""
    if not HAS_FREECAD:
        return None
    # X_m = Z_cad - 50.0
    # Y_m = X_cad + 403.5635478801571
    # Z_m = Y_cad + 3834.154260829799 + ram_z_mm
    return App.Matrix(
        0.0, 0.0, 1.0, -50.0,
        1.0, 0.0, 0.0, 403.5635478801571,
        0.0, 1.0, 0.0, 3834.154260829799 + float(ram_z_mm),
        0.0, 0.0, 0.0, 1.0,
    )


def load_native_die(file_path: str | Path) -> LoadedTool:
    """Load native FreeCAD die CAD model and transform into Machine Space (Z_top = 0.0)."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Die CAD file not found: {p}")
    if not HAS_FREECAD:
        raise RuntimeError("FreeCAD runtime is required to load native CAD die")

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
        mat = _matrix_for_die()
        machine_shape = cad_shape.copy()
        machine_shape.transformShape(mat)

        if not machine_shape.isValid() or machine_shape.Volume <= 0:
            raise ValueError(f"Transformed die shape is invalid or non-solid: {p}")

        return LoadedTool(
            tool_id=p.stem,
            kind="DIE",
            file_path=str(p),
            cad_shape=cad_shape,
            machine_shape=machine_shape,
            tip_radius_mm=0.0,
            included_angle_deg=85.0,
            v_opening_mm=32.0,
            shoulder_radius_mm=2.0,
            height_mm=60.0,
            length_mm=100.0,
        )
    finally:
        App.closeDocument(doc.Name)


def load_native_punch(file_path: str | Path, ram_z_mm: float = 0.0) -> LoadedTool:
    """Load native FreeCAD punch CAD model and transform into Machine Space (tip at ram_z_mm)."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Punch CAD file not found: {p}")
    if not HAS_FREECAD:
        raise RuntimeError("FreeCAD runtime is required to load native CAD punch")

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
        mat = _matrix_for_punch(ram_z_mm)
        machine_shape = cad_shape.copy()
        machine_shape.transformShape(mat)

        if not machine_shape.isValid() or machine_shape.Volume <= 0:
            raise ValueError(f"Transformed punch shape is invalid or non-solid: {p}")

        return LoadedTool(
            tool_id=p.stem,
            kind="PUNCH",
            file_path=str(p),
            cad_shape=cad_shape,
            machine_shape=machine_shape,
            tip_radius_mm=0.8,
            included_angle_deg=30.0,
            v_opening_mm=0.0,
            shoulder_radius_mm=0.0,
            height_mm=134.0,
            length_mm=100.0,
        )
    finally:
        App.closeDocument(doc.Name)


def set_punch_ram_z(punch: LoadedTool, ram_z_mm: float) -> LoadedTool:
    """Create a new LoadedTool punch instance with updated ram_z_mm coordinate."""
    if punch.kind != "PUNCH":
        raise ValueError("Can only set ram_z for PUNCH tool")
    mat = _matrix_for_punch(ram_z_mm)
    machine_shape = punch.cad_shape.copy()
    machine_shape.transformShape(mat)
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
        height_mm=punch.height_mm,
        length_mm=punch.length_mm,
    )
