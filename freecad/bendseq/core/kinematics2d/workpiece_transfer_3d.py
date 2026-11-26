"""C2 — continuous rigid 3D workpiece transfer, geometry only.

Uses unchanged FoldState geometry and existing PlacementTransform3D.
A smooth rigid path is NOT a collision-free or machine-accessible path.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from .workpiece_placement_3d import (
    PlacementTransform3D, WorkpiecePlacement3D,
    _mat_mult, _mat_transpose,
)
from .workpiece_placement_validation_3d import (
    validate_rigid_3d, PlacementValidationError,
)


def _quaternion(rotation):
    R = rotation
    trace = sum(R[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace+1.0)*2
        q = (s/4, (R[2][1]-R[1][2])/s,
             (R[0][2]-R[2][0])/s, (R[1][0]-R[0][1])/s)
    else:
        i = max(range(3), key=lambda j: R[j][j])
        if i == 0:
            s = math.sqrt(max(0,1+R[0][0]-R[1][1]-R[2][2]))*2
            q = ((R[2][1]-R[1][2])/s, s/4,
                 (R[0][1]+R[1][0])/s, (R[0][2]+R[2][0])/s)
        elif i == 1:
            s = math.sqrt(max(0,1+R[1][1]-R[0][0]-R[2][2]))*2
            q = ((R[0][2]-R[2][0])/s,
                 (R[0][1]+R[1][0])/s, s/4, (R[1][2]+R[2][1])/s)
        else:
            s = math.sqrt(max(0,1+R[2][2]-R[0][0]-R[1][1]))*2
            q = ((R[1][0]-R[0][1])/s,
                 (R[0][2]+R[2][0])/s,
                 (R[1][2]+R[2][1])/s, s/4)
    n = math.sqrt(sum(v*v for v in q))
    return tuple(v/n for v in q)


def _quat_matrix(q):
    w,x,y,z = q
    return (
        (1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)),
        (2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)),
        (2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)),
    )


def _slerp(q0,q1,u):
    dot = sum(a*b for a,b in zip(q0,q1))
    if dot < 0:
        q1 = tuple(-v for v in q1)
        dot = -dot
    dot = max(-1.0,min(1.0,dot))
    if dot > 0.9995:
        q = tuple((1-u)*a+u*b for a,b in zip(q0,q1))
    else:
        theta = math.acos(dot)
        s = math.sin(theta)
        q = tuple(math.sin((1-u)*theta)/s*a +
                  math.sin(u*theta)/s*b for a,b in zip(q0,q1))
    n = math.sqrt(sum(v*v for v in q))
    return tuple(v/n for v in q)


@dataclass(frozen=True)
class Transfer3D:
    start: PlacementTransform3D
    end: PlacementTransform3D
    duration_s: float
    angular_displacement_deg: float
    translation_distance_mm: float
    collision_validated: bool = False

    def pose_at(self, elapsed_s: float) -> PlacementTransform3D:
        if not math.isfinite(elapsed_s):
            raise ValueError("Invalid time")
        u = min(1.0, max(0.0, elapsed_s/self.duration_s))
        if u == 0:
            return self.start
        if u == 1:
            return self.end
        q = _slerp(_quaternion(self.start.rotation),
                   _quaternion(self.end.rotation), u)
        trans = tuple((1-u)*a+u*b for a,b in zip(
            self.start.translation, self.end.translation))
        return PlacementTransform3D(_quat_matrix(q), trans)


def build_transfer_3d(start, end, *, max_linear_mm_s,
                      max_angular_deg_s, min_duration_s=0.05):
    """Translation and rotation limits apply to frame pose, not point speed.

    Point velocities induced by rotation depend on distance from the origin.
    Collision validation remains separate and mandatory for Full Cycle.
    """
    for v in (max_linear_mm_s,max_angular_deg_s,min_duration_s):
        if not math.isfinite(v) or v <= 0:
            raise ValueError("Speed limits/duration must be positive finite")
    validate_rigid_3d(start)
    validate_rigid_3d(end)
    relative = _mat_mult(end.rotation,_mat_transpose(start.rotation))
    trace = sum(relative[i][i] for i in range(3))
    angle = math.degrees(math.acos(max(-1.0,min(1.0,(trace-1)/2))))
    distance = math.dist(start.translation,end.translation)
    duration = max(min_duration_s,distance/max_linear_mm_s,
                   angle/max_angular_deg_s)
    return Transfer3D(start,end,duration,angle,distance)


def build_bend_transfer(state, from_bend_id, to_bend_id, *,
                        max_linear_mm_s,max_angular_deg_s,
                        support_plane_zm=0.0):
    """Pose-to-pose transfer at an unchanged, completed-bend FoldState."""
    if from_bend_id == to_bend_id:
        raise PlacementValidationError("Bend IDs must differ")
    if from_bend_id not in state.bends or to_bend_id not in state.bends:
        raise KeyError("Unknown bend")
    bend = state.bends[from_bend_id]
    if not math.isclose(state.angles[from_bend_id],
                        bend.angle_deg,abs_tol=1e-7):
        raise PlacementValidationError("Previous bend is incomplete")
    start = WorkpiecePlacement3D.compute_placement(
        state,from_bend_id,support_plane_zm=support_plane_zm)
    end = WorkpiecePlacement3D.compute_placement(
        state,to_bend_id,support_plane_zm=support_plane_zm)
    return build_transfer_3d(
        start,end,max_linear_mm_s=max_linear_mm_s,
        max_angular_deg_s=max_angular_deg_s)
