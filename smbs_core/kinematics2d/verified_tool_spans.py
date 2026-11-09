"""Verify fixed physical tool spans from the SAME native FreeCAD loaders as 3D.

A catalog length is not geometry evidence. Virtual tool stretching is not
allowed for this physical benchmark. Geometry must be sourced from verified
FCStd tooling and its actual machine-space Part.Shape BoundBox.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VerifiedToolSpans:
    punch_span_xm: tuple[float, float]
    die_span_xm: tuple[float, float]
    punch_source: str
    die_source: str

    def to_dict(self) -> dict:
        return {
            "status": "SOURCE_CAD_FIXED_TOOL_MACHINE_BBOX_VERIFIED",
            "punch_span_xm": list(self.punch_span_xm),
            "die_span_xm": list(self.die_span_xm),
            "punch_source": self.punch_source,
            "die_source": self.die_source,
            "note": "X range from native transformed fixed CAD solids; not a continuous trajectory proof",
        }


def span_from_native_shape(tool: Any, name: str) -> tuple[float, float]:
    """Get the actual transformed machine-X extents; fail closed on all gaps."""
    if tool is None or getattr(tool, "is_virtual_length", True):
        raise ValueError(f"{name}: native fixed-length CAD tool required")
    shape = getattr(tool, "machine_shape", None)
    if shape is None:
        raise ValueError(f"{name}: machine-space solid missing")
    if shape.isNull() or not shape.isValid() or float(shape.Volume) <= 0:
        raise ValueError(f"{name}: native machine-space solid is invalid")
    bb = getattr(shape, "BoundBox", None)
    if bb is None:
        raise ValueError(f"{name}: native machine-space BoundBox missing")
    a, b = float(bb.XMin), float(bb.XMax)
    if not (isfinite(a) and isfinite(b) and b > a + 1e-3):
        raise ValueError(f"{name}: invalid machine X extents")
    nominal = getattr(tool, "span_xm", None)
    if nominal is None or len(nominal) != 2:
        raise ValueError(f"{name}: source-loader span missing")
    n0, n1 = map(float, nominal)
    if not all(isfinite(v) for v in (n0, n1)):
        raise ValueError(f"{name}: nonfinite source-loader span")
    if abs(a - n0) > 1e-3 or abs(b - n1) > 1e-3:
        raise ValueError(
            f"{name}: transformed CAD X extents {(a, b)} disagree with loader span {(n0, n1)}"
        )
    return a, b


def verified_fixed_tool_spans(tooling: Any, *, ram_z_mm: float = 0.0) -> VerifiedToolSpans:
    """Load the selected verified FCStd source models in machine coordinates.

    Does not infer source geometry from catalog length or tool names.
    Does not fabricate a 100mm fallback. Runs only in a FreeCAD environment.
    """
    from smbs_core.kinematics2d.physical_validation_25d import _source_path
    from smbs_core.kinematics2d.native_tool_loader import (
        load_native_punch, load_native_die,
    )

    punch_source = _source_path(getattr(tooling, "punch", None))
    die_source = _source_path(getattr(tooling, "die", None))
    if punch_source is None or die_source is None:
        raise ValueError("Both selected tools must have GEOMETRY_VERIFIED FCStd source files")
    punch = load_native_punch(punch_source, ram_z_mm=float(ram_z_mm))
    die = load_native_die(die_source)
    return VerifiedToolSpans(
        punch_span_xm=span_from_native_shape(punch, "PUNCH"),
        die_span_xm=span_from_native_shape(die, "DIE"),
        punch_source=str(Path(punch_source).resolve()),
        die_source=str(Path(die_source).resolve()),
    )
