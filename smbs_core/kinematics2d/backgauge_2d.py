"""Explicit machine-space 2D backgauge stop model (s,z section).

The stop is a finite rectangular profile in the machine section. A real
backgauge may have two fingers, X/R/Z axes, and complex 3D geometry; this
module does NOT invent any missing axes or claim 3D completeness.

No implicit default stop location: every bend must specify a target (s,z).
The existing Stage B polygon-contact oracle performs the actual overlap tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class BackgaugePosition2D:
    s_mm: float
    z_mm: float

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
               for v in (self.s_mm, self.z_mm)):
            raise ValueError("backgauge position must be finite numeric s,z")

    def to_dict(self) -> dict[str, float]:
        return {"s_mm": float(self.s_mm), "z_mm": float(self.z_mm)}


@dataclass(frozen=True)
class BackgaugeModel2D:
    width_s_mm: float
    height_z_mm: float
    positions: Mapping[str, BackgaugePosition2D]
    part_label: str = ""

    def __post_init__(self):
        for val in (self.width_s_mm, self.height_z_mm):
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val) or val <= 0:
                raise ValueError("backgauge stop dimensions must be positive and finite")
        if not isinstance(self.part_label, str):
            raise ValueError("backgauge part_label must be text")
        if not isinstance(self.positions, Mapping):
            raise ValueError("backgauge positions must be a mapping")
        if any(not isinstance(bid, str) or not bid or not isinstance(p, BackgaugePosition2D)
               for bid, p in self.positions.items()):
            raise ValueError("invalid bend-to-backgauge position mapping")

    def position_for(self, bend_id: str) -> BackgaugePosition2D:
        try:
            return self.positions[bend_id]
        except KeyError:
            raise ValueError(f"backgauge target missing for bend {bend_id}") from None

    def polygon_for(self, bend_id: str) -> tuple[tuple[float, float], ...]:
        point = self.position_for(bend_id)
        a = point.s_mm - self.width_s_mm / 2
        b = point.s_mm + self.width_s_mm / 2
        c = point.z_mm - self.height_z_mm / 2
        d = point.z_mm + self.height_z_mm / 2
        return ((a, c), (b, c), (b, d), (a, d))

    def to_dict(self) -> dict[str, Any]:
        return {
            "width_s_mm": float(self.width_s_mm),
            "height_z_mm": float(self.height_z_mm),
            "positions": {bid: p.to_dict() for bid, p in self.positions.items()},
            "part_label": self.part_label,
            "geometry_evidence": "2D_RECTANGLE_ONLY_NOT_3D_VERIFIED",
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BackgaugeModel2D":
        if not isinstance(data, Mapping):
            raise ValueError("backgauge config must be an object")
        positions = data.get("positions", {})
        if not isinstance(positions, Mapping):
            raise ValueError("backgauge positions must be an object")
        return BackgaugeModel2D(
            width_s_mm=float(data["width_s_mm"]),
            height_z_mm=float(data["height_z_mm"]),
            positions={
                bid: BackgaugePosition2D(float(p["s_mm"]), float(p["z_mm"]))
                for bid, p in positions.items()
            },
            part_label=str(data.get("part_label", "")),
        )
