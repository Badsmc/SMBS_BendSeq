"""
bend_graph.py - Canonical Bend Record & Bend Relationship Graph for SMBS
"""

from typing import Dict, Any, List, Optional, Tuple
import math


class IncompleteBendError(ValueError):
    """Raised when a BendRecord lacks mandatory geometric parameters."""
    pass


class BendRecord:
    """
    Canonical Bend Record.
    signed_angle and length are mandatory geometric parameters.
    thickness and radius are optional (None means resolved from sm_client/FeatureExtractor).
    Explicit values are treated as overrides for tests and debugging only.

    signed_angle: Kinematic rotation angle of the child panel around `axis_direction` 
    relative to the parent panel, in degrees. Sign convention: right-hand 
    rule around `axis_direction`. NOT to be confused with SheetMetal's 
    `is_down` flag, which is a separate technological property.
    """
    def __init__(
        self,
        bend_id: str,
        feature_id: str,
        parent_panel_id: str,
        child_panel_id: str,
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle: float,
        radius: Optional[float] = None,
        thickness: Optional[float] = None,
        bend_sign: int = 1,
        flat_axis: Optional[Tuple[float, float, float]] = None,
        flat_center: Optional[Tuple[float, float, float]] = None,
        length: float = 100.0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.id = bend_id
        self.feature_id = feature_id
        self.parent_panel_id = parent_panel_id
        self.child_panel_id = child_panel_id
        self.axis_origin = axis_origin
        self.axis_direction = axis_direction
        self.signed_angle = signed_angle
        self.radius = radius
        self.thickness = thickness
        self.bend_sign = bend_sign
        self.flat_axis = flat_axis or axis_direction
        self.flat_center = flat_center or axis_origin
        self.length = length
        self.metadata = metadata or {}

        self.validate()

    def validate(self) -> None:
        if self.radius is not None and self.radius <= 0.0:
            raise IncompleteBendError(f"Bend {self.id} has invalid radius {self.radius}")
        if self.thickness is not None and self.thickness <= 0.0:
            raise IncompleteBendError(f"Bend {self.id} has invalid thickness {self.thickness}")
        if self.signed_angle is None or abs(self.signed_angle) > 180.0:
            raise IncompleteBendError(f"Bend {self.id} has out of range signed angle {self.signed_angle}°")


    @property
    def unsigned_angle(self) -> float:
        return abs(self.signed_angle)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "feature_id": self.feature_id,
            "parent_panel_id": self.parent_panel_id,
            "child_panel_id": self.child_panel_id,
            "axis_origin": list(self.axis_origin),
            "axis_direction": list(self.axis_direction),
            "signed_angle": self.signed_angle,
            "angle": self.unsigned_angle,
            "radius": self.radius,
            "thickness": self.thickness,
            "bend_sign": self.bend_sign,
            "length": self.length,
            "axis": list(self.axis_direction),
            "origin": list(self.axis_origin)
        }

    def __repr__(self) -> str:
        return f"<BendRecord {self.id} angle={self.signed_angle:+.1f}° ({self.parent_panel_id}->{self.child_panel_id})>"


class BendGraph:
    def __init__(self):
        self._bends: Dict[str, BendRecord] = {}

    @property
    def bends(self) -> Dict[str, BendRecord]:
        return self._bends

    def add_bend(self, bend: BendRecord) -> None:
        bend.validate()
        self._bends[bend.id] = bend

    def get_bend(self, bend_id: str) -> Optional[BendRecord]:
        return self._bends.get(bend_id)

    def all_bends(self) -> List[BendRecord]:
        return list(self._bends.values())

    def __len__(self) -> int:
        return len(self._bends)

    def __contains__(self, bend_id: str) -> bool:
        return bend_id in self._bends
