"""Shared, FreeCAD-independent ram Z reference for preview and D6.3.4.

All positions are in MACHINE Z, mm; punch moves downward toward negative Z.
The user-configured clearance is relative to the contact height, not a
hard-coded machine absolute +40/+50. BOTTOM is the finished bend stroke,
RETRACT starts at BOTTOM and ends at the same upper point as APPROACH.
This is geometry for a sampled kinematic path, not a safety certification.
"""
from __future__ import annotations

from math import isfinite


def ram_cycle_positions(
    contact_z_mm: float, bottom_z_mm: float, initial_clearance_mm: float,
) -> dict[str, float]:
    contact = float(contact_z_mm)
    bottom = float(bottom_z_mm)
    clearance = float(initial_clearance_mm)
    if not all(isfinite(x) for x in (contact, bottom, clearance)):
        raise ValueError("nonfinite ram Z coordinate or clearance")
    if clearance <= 0.0:
        raise ValueError("initial ram clearance must be positive")
    if bottom > contact + 1e-7:
        raise ValueError("bottom ram position cannot exceed contact Z")
    upper = contact + clearance
    return {
        "approach_start_z_mm": upper,
        "contact_z_mm": contact,
        "bottom_z_mm": bottom,
        "retract_start_z_mm": bottom,
        "retract_end_z_mm": upper,
        "initial_clearance_mm": clearance,
    }
