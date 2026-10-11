"""
tooling.py - Data models for Machine, Tooling and Datums (headless core).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

Vec3 = Tuple[float, float, float]


class ToolingKind(Enum):
    PUNCH = "punch"
    DIE = "die"
    MACHINE_BED = "machine_bed"
    MACHINE_FRAME = "machine_frame"
    RAM = "ram"
    BACKGAUGE = "backgauge"


class DatumRole(Enum):
    MOUNTING_PLANE = "mounting_plane"
    WORKING_AXIS = "working_axis"
    TIP_POINT = "tip_point"
    V_CENTER = "v_center"
    V_OPENING = "v_opening"
    STOP_FACE = "stop_face"
    REFERENCE_PLANE = "reference_plane"


class GeometryStatus(Enum):
    REAL = "real"
    SIMPLIFIED = "simplified"
    MISSING = "missing"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value == other.lower()
        return super().__eq__(other)


class AlignmentStatus(Enum):
    ALIGNED = "ALIGNED"
    MISALIGNED = "MISALIGNED"
    CANNOT_VERIFY = "CANNOT_VERIFY"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value == other.upper()
        return super().__eq__(other)


DatumKind = Literal["point", "axis", "plane", "face"]
DatumSource = Literal["sidecar", "inferred"]


def _norm(v: Vec3) -> Vec3:
    m = math.sqrt(sum(c * c for c in v))
    if m < 1e-12:
        raise ValueError(f"Zero-length direction in datum: {v}")
    return (v[0] / m, v[1] / m, v[2] / m)


@dataclass
class Datum:
    role: DatumRole
    kind: DatumKind
    geometry: Tuple[Any, ...]
    tolerance_mm: float = 0.05
    source: DatumSource = "sidecar"
    confirmed: bool = True
    value: Optional[float] = None

    @property
    def usable_for_verification(self) -> bool:
        return self.source == "sidecar" or self.confirmed

    @property
    def origin(self) -> Vec3:
        return tuple(float(c) for c in self.geometry[0])  # type: ignore

    @property
    def direction(self) -> Vec3:
        if self.kind == "point":
            raise ValueError("Point datum has no direction")
        return _norm(tuple(float(c) for c in self.geometry[1]))  # type: ignore

    @property
    def x_direction(self) -> Optional[Vec3]:
        if self.kind == "plane" and len(self.geometry) > 2 and self.geometry[2] is not None:
            return _norm(tuple(float(c) for c in self.geometry[2]))  # type: ignore
        return None

    @staticmethod
    def from_json(role: DatumRole, data: Dict[str, Any], source: DatumSource = "sidecar") -> "Datum":
        kind = data.get("kind", "point")
        tol = float(data.get("tolerance_mm", 0.05))
        if kind == "point":
            geom: Tuple[Any, ...] = (tuple(data["position"]),)
        elif kind == "axis":
            geom = (tuple(data["origin"]), tuple(data["direction"]))
        elif kind in ("plane", "face"):
            xd = data.get("x_direction")
            geom = (tuple(data["origin"]), tuple(data["normal"]), tuple(xd)) if xd else (tuple(data["origin"]), tuple(data["normal"]))
        else:
            raise ValueError(f"Unknown datum kind '{kind}' for {role.value}")
        return Datum(role=role, kind=kind, geometry=geom, tolerance_mm=tol, source=source,
                     confirmed=bool(data.get("confirmed", source == "sidecar")),
                     value=data.get("value"))


class Tooling:
    def __init__(
        self,
        id: str,
        kind: ToolingKind,
        source_file: str,
        shape: Any,
        local_frame: Any,
        geometry_status: GeometryStatus = GeometryStatus.MISSING,
        datums: Optional[Dict[DatumRole, Datum]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        diagnostics: Optional[List[str]] = None
    ):
        self.id = id
        self.kind = kind
        self.source_file = source_file
        self.shape = shape
        self.local_frame = local_frame

        if isinstance(geometry_status, str):
            geometry_status = GeometryStatus(geometry_status.lower())
        self.geometry_status = geometry_status

        self.datums = datums if datums is not None else {}
        self.metadata = metadata if metadata is not None else {}
        self.diagnostics = diagnostics if diagnostics is not None else []

        if self.shape is None and self.geometry_status != GeometryStatus.MISSING:
            self.diagnostics.append(f"{self.id}: shape is None -> geometry_status forced to MISSING")
            self.geometry_status = GeometryStatus.MISSING

    def has_verified_datums(self, roles: List[DatumRole]) -> bool:
        return all(r in self.datums and self.datums[r].usable_for_verification for r in roles)


@dataclass
class MachineConfiguration:
    id: str
    bed_top_plane: Datum
    ram_mount_plane: Datum
    ram_stroke_mm: float
    ram_direction: Vec3
    backgauge_axes: Dict[str, Dict[str, Any]]
    max_tonnage_kn: float
    max_bend_length_mm: float
    source_file: Optional[str] = None
    lateral_tolerance_mm: float = 0.05
    angular_tolerance_deg: float = 0.1
    frame: Optional[Tooling] = None
    backgauge_finger: Optional[Tooling] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
