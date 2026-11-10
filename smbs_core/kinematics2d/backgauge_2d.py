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
    automatic: bool = False
    edge_clearance_mm: float = 0.0
    initial_feed_preview_mm: float = 0.0

    def __post_init__(self):
        for val in (self.width_s_mm, self.height_z_mm):
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val) or val <= 0:
                raise ValueError("backgauge stop dimensions must be positive and finite")
        if not isinstance(self.automatic, bool):
            raise ValueError("automatic must be boolean")
        if (isinstance(self.edge_clearance_mm, bool)
                or not isinstance(self.edge_clearance_mm, (int, float))
                or not isfinite(self.edge_clearance_mm)
                or self.edge_clearance_mm < 0):
            raise ValueError("edge_clearance_mm must be finite and nonnegative")
        if (isinstance(self.initial_feed_preview_mm, bool)
                or not isinstance(self.initial_feed_preview_mm, (int, float))
                or not isfinite(self.initial_feed_preview_mm)
                or self.initial_feed_preview_mm < 0):
            raise ValueError("initial_feed_preview_mm must be finite and nonnegative")
        if not isinstance(self.part_label, str):
            raise ValueError("backgauge part_label must be text")
        if not isinstance(self.positions, Mapping):
            raise ValueError("backgauge positions must be a mapping")
        if any(not isinstance(bid, str) or not bid or not isinstance(p, BackgaugePosition2D)
               for bid, p in self.positions.items()):
            raise ValueError("invalid bend-to-backgauge position mapping")
        if any(p.s_mm - self.width_s_mm / 2 <= 0
               for p in self.positions.values()):
            raise ValueError(
                "backgauge stop must lie entirely on +s/right machine side"
            )

    def position_for(self, bend_id: str) -> BackgaugePosition2D:
        try:
            return self.positions[bend_id]
        except KeyError:
            raise ValueError(f"backgauge target missing for bend {bend_id}") from None

    def polygon_for(self, bend_id: str) -> tuple[tuple[float, float], ...]:
        point = self.position_for(bend_id)
        return self.polygon_at(point)

    def polygon_at(self, point: BackgaugePosition2D) -> tuple[tuple[float, float], ...]:
        a = point.s_mm - self.width_s_mm / 2
        b = point.s_mm + self.width_s_mm / 2
        c = point.z_mm - self.height_z_mm / 2
        d = point.z_mm + self.height_z_mm / 2
        return ((a, c), (b, c), (b, d), (a, d))

    def resolve_for_state(
        self,
        fold_state: Any,
        panel_vertices_xy: Mapping[str, Any],
        bend_id: str,
        *,
        desired_midpoint_xm: float = 0.0,
    ) -> tuple[BackgaugePosition2D, float, str]:
        """Resolve the stop from the fixed-panel cut edge at the active bend.

        For automatic mode the same machine placement/section used at the
        beginning of Stage B determines the fixed panel edge in the cutting
        plane. The selected extreme MUST lie on the fixed panel; never take
        a global part BBox or invent a missing edge. This is 2D setup
        evidence only and cannot infer physically usable rear finger access.
        """
        if not self.automatic:
            return self.position_for(bend_id), float("nan"), "MANUAL_TARGET"
        from .workpiece_placement_3d import WorkpiecePlacement3D
        from .machine_section_3d import compute_machine_cutting_plane_section

        if bend_id not in fold_state.bends:
            raise ValueError("unknown bend: " + bend_id)
        zone = fold_state.bends[bend_id].bend_zone
        if zone is None:
            raise ValueError("missing bend zone: " + bend_id)
        plc = WorkpiecePlacement3D.compute_placement(
            fold_state, bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            support_plane_zm=float(zone.outer_radius_mm),
            align_bisector=False,
            align_bend_arc_center=True,
            rear_panel_vertices_xy=panel_vertices_xy,
        )
        section = compute_machine_cutting_plane_section(
            fold_state, panel_vertices_xy, plc,
            active_bend_id=bend_id, segments=32,
            target_xm=float(desired_midpoint_xm),
        )
        poly = section.get("fixed_polygon") or ()
        if len(poly) < 4:
            raise ValueError("no usable fixed-panel edge in gauge cutting plane: " + bend_id)
        # Machine contract: +s/right = inside press with rear stop;
        # -s/left = operator side. NEVER choose the farthest ABSOLUTE
        # extent and silently move the gauge to the operator side.
        all_s = [float(p[0]) for p in poly]
        edge_s = max(all_s)
        if not isfinite(edge_s) or edge_s <= 0.5:
            raise ValueError("no positive rear edge for bend: " + bend_id)
        # At flat setup, the cut panel is thickness-bearing; select
        # the edge's midpoint in Z rather than a global model elevation.
        edge_points_z = [float(z) for x, z in poly
                         if abs(float(x) - edge_s) <= 1e-3]
        if not edge_points_z:
            raise ValueError("fixed panel edge is not resolved in section: " + bend_id)
        edge_z = (min(edge_points_z) + max(edge_points_z)) / 2.0
        position = BackgaugePosition2D(
            edge_s + self.width_s_mm / 2 + self.edge_clearance_mm,
            edge_z,
        )
        if position.s_mm <= 0.0:
            raise ValueError("backgauge_target_on_operator_side")
        return position, edge_s, "AUTO_REAR_EDGE_RIGHT_2D_UNVERIFIED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "width_s_mm": float(self.width_s_mm),
            "height_z_mm": float(self.height_z_mm),
            "positions": {bid: p.to_dict() for bid, p in self.positions.items()},
            "part_label": self.part_label,
            "automatic": self.automatic,
            "edge_clearance_mm": float(self.edge_clearance_mm),
            "initial_feed_preview_mm": float(self.initial_feed_preview_mm),
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
            automatic=data.get("automatic", False),
            edge_clearance_mm=float(data.get("edge_clearance_mm", 0.0)),
            initial_feed_preview_mm=float(data.get("initial_feed_preview_mm", 0.0)),
        )
