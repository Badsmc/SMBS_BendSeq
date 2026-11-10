"""Pure-math orbit camera for the PressBrakeSim projected 3D viewer.

This module changes view coordinates only; it never mutates FoldState geometry.
Angles are expressed in degrees. Yaw rotates about Z, pitch about X and
roll about the view direction (2D screen plane).
"""
from __future__ import annotations

import math


def orbit_project(point, yaw_deg=35.0, pitch_deg=25.0, roll_deg=0.0):
    """Return (screen_x, screen_y, depth) after orthographic camera rotation."""
    x, y, z = (float(v) for v in point)
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)

    # Yaw around the world Z axis.
    x, y = cy * x - sy * y, sy * x + cy * y
    # Pitch around the camera-aligned X axis.
    y, z = cp * y - sp * z, sp * y + cp * z
    # Roll about the viewing axis.
    x, z = cr * x - sr * z, sr * x + cr * z
    return x, -z, y


def clamp_pitch(pitch_deg):
    """Constrain pitch away from singular pole positions."""
    return max(-89.0, min(89.0, float(pitch_deg)))
