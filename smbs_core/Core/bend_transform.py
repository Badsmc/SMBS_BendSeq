"""
bend_transform.py - Precise Subtree Kinematic Rigid-Body Rotations (SMBS Core)
"""

import math
from typing import Tuple, Set, Optional, Any
from smbs_core.geometry_backend.base import GeometryBackend


class KinematicRotateError(Exception):
    """Raised when kinematic panel rotation fails."""
    pass


class BendTransform:
    @staticmethod
    def rodrigues_rotation_matrix(
        axis_direction: Tuple[float, float, float],
        angle_deg: float
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        ux, uy, uz = axis_direction
        mag = math.sqrt(ux*ux + uy*uy + uz*uz)
        if mag < 1e-12:
            raise ValueError(f"Invalid zero-length rotation axis direction: {axis_direction}")
        ux, uy, uz = ux/mag, uy/mag, uz/mag

        R00 = cos_a + ux*ux*(1 - cos_a)
        R01 = ux*uy*(1 - cos_a) - uz*sin_a
        R02 = ux*uz*(1 - cos_a) + uy*sin_a

        R10 = uy*ux*(1 - cos_a) + uz*sin_a
        R11 = cos_a + uy*uy*(1 - cos_a)
        R12 = uy*uz*(1 - cos_a) - ux*sin_a

        R20 = uz*ux*(1 - cos_a) - uy*sin_a
        R21 = uz*uy*(1 - cos_a) + ux*sin_a
        R22 = cos_a + uz*uz*(1 - cos_a)

        return (
            (R00, R01, R02),
            (R10, R11, R12),
            (R20, R21, R22)
        )

    @staticmethod
    def transform_point(
        point: Tuple[float, float, float],
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float
    ) -> Tuple[float, float, float]:
        ox, oy, oz = axis_origin
        px, py, pz = point

        rel_p = (px - ox, py - oy, pz - oz)
        R = BendTransform.rodrigues_rotation_matrix(axis_direction, signed_angle_deg)

        rx = R[0][0]*rel_p[0] + R[0][1]*rel_p[1] + R[0][2]*rel_p[2]
        ry = R[1][0]*rel_p[0] + R[1][1]*rel_p[1] + R[1][2]*rel_p[2]
        rz = R[2][0]*rel_p[0] + R[2][1]*rel_p[1] + R[2][2]*rel_p[2]

        return (round(ox + rx, 6), round(oy + ry, 6), round(oz + rz, 6))

    @staticmethod
    def transform_vector(
        vector: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float
    ) -> Tuple[float, float, float]:
        R = BendTransform.rodrigues_rotation_matrix(axis_direction, signed_angle_deg)
        vx, vy, vz = vector
        rx = R[0][0]*vx + R[0][1]*vy + R[0][2]*vz
        ry = R[1][0]*vx + R[1][1]*vy + R[1][2]*vz
        rz = R[2][0]*vx + R[2][1]*vy + R[2][2]*vz
        return (round(rx, 6), round(ry, 6), round(rz, 6))

    @staticmethod
    def rotate_subtree(
        arg1: Any,
        arg2: Any,
        arg3: Any = None,
        arg4: Any = None,
        arg5: Any = None,
        arg6: Any = None,
        arg7: Any = None
    ) -> Any:
        if isinstance(arg1, GeometryBackend):
            backend = arg1
            shape = arg2
            axis_origin = arg3
            axis_direction = arg4
            signed_angle_deg = arg5
            moving_panels = arg6
            panel_graph = arg7
        else:
            backend = None
            shape = arg1
            axis_origin = arg2
            axis_direction = arg3
            signed_angle_deg = arg4
            moving_panels = None
            panel_graph = None

        if shape is None:
            raise KinematicRotateError("Cannot rotate subtree: target shape is None.")

        if backend is not None and hasattr(backend, 'rotate_subtree'):
            result = backend.rotate_subtree(
                shape=shape,
                axis_origin=axis_origin,
                axis_direction=axis_direction,
                signed_angle_deg=signed_angle_deg,
                moving_panels=moving_panels,
                panel_graph=panel_graph
            )
            if result is None:
                raise KinematicRotateError("Backend rotate_subtree returned None.")
            return result

        if hasattr(shape, 'Placement'): # FreeCAD shape
            import FreeCAD
            try:
                v_orig = FreeCAD.Vector(*axis_origin)
                v_dir = FreeCAD.Vector(*axis_direction)
                rot = FreeCAD.Rotation(v_dir, signed_angle_deg)

                trsf_shape = shape.copy()
                trsf_shape.Placement = FreeCAD.Placement(v_orig, rot).multiply(shape.Placement)
                return trsf_shape
            except Exception as e:
                raise KinematicRotateError(f"FreeCAD shape Placement rotation failed: {e}") from e

        raise KinematicRotateError(f"No backend or FreeCAD shape available to perform rotate_subtree (signed_angle={signed_angle_deg}).")

