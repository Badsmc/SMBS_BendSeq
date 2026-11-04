"""Rigid placement of the folded 2.5D sheet in press-machine coordinates.

FoldState owns the relative bend geometry. This view-only transform positions
that unmodified geometry around the selected V-groove for a symmetric preview.
It is NOT a contact solver, collision validator or physical workpiece flip.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, isfinite, radians, sin


class WorkpiecePoseError(ValueError):
    pass


@dataclass(frozen=True)
class WorkpiecePose2D:
    pivot: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    mirror_z: bool = False
    shift: tuple[float, float] = (0.0, 0.0)
    mode: str = "IDENTITY"

    def apply(self, point):
        s, z = float(point[0]) - self.pivot[0], float(point[1]) - self.pivot[1]
        if self.mirror_z:
            z = -z
        a = radians(self.rotation_deg)
        return (self.pivot[0] + self.shift[0] + cos(a) * s - sin(a) * z,
                self.pivot[1] + self.shift[1] + sin(a) * s + cos(a) * z)

    def inverse(self, point):
        s = float(point[0]) - self.pivot[0] - self.shift[0]
        z = float(point[1]) - self.pivot[1] - self.shift[1]
        a = radians(self.rotation_deg)
        ls, lz = cos(a) * s + sin(a) * z, -sin(a) * s + cos(a) * z
        if self.mirror_z:
            lz = -lz
        return (self.pivot[0] + ls, self.pivot[1] + lz)

    def points(self, points):
        return tuple(self.apply(p) for p in points)


def symmetric_v_pose(section):
    """Return preview placement with equal flange elevations about vertical.

    Preserve the relative angle, zone radii and panel lengths. For opposite
    bends, mirroring denotes an assumed workpiece reorientation, not a real
    gripping operation.
    """
    try:
        fixed = section["fixed_polygon"]
        moving = section["moving_polygon"]
        neutral = section["neutral_curve"]
        target = float(section["target_angle_deg"])
        fv = (float(fixed[0][0]) - float(fixed[1][0]),
              float(fixed[0][1]) - float(fixed[1][1]))
        mv = (float(moving[1][0]) - float(moving[0][0]),
              float(moving[1][1]) - float(moving[0][1]))
        if hypot(*fv) <= 1e-7 or hypot(*mv) <= 1e-7 or not neutral:
            raise WorkpiecePoseError("Degenerate flange or missing bend center")
        if not isfinite(target):
            raise WorkpiecePoseError("Nonfinite target bend angle")
        pivot = tuple(float(v) for v in neutral[len(neutral) // 2])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WorkpiecePoseError("Invalid cross-section for symmetric preview") from exc

    mirror = target < -1e-9
    if mirror:
        fv, mv = (fv[0], -fv[1]), (mv[0], -mv[1])
    if fv[0] >= -1e-7 or mv[0] < -1e-7:
        raise WorkpiecePoseError("Unsupported flange orientation in current 2.5D section")
    left_rise = atan2(fv[1], -fv[0])
    right_rise = atan2(mv[1], mv[0])
    rotation = (left_rise - right_rise) / 2.0
    return WorkpiecePose2D(
        pivot=pivot, rotation_deg=degrees(rotation), mirror_z=mirror,
        mode="ASSUMED_FLIP_SYMMETRIC_V_PREVIEW" if mirror else "SYMMETRIC_V_PREVIEW",
    )
