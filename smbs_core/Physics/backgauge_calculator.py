"""
backgauge_calculator.py - Hybrid Strategy for Backgauge / Part Basing Calculation
"""

import math
from typing import Dict, Any, Tuple, Optional
from .machine_kinematics import PressBrakeMachine


class BackgaugeCalculator:
    """
    Calculates backgauge positions (X, R, Z1, Z2 axes) for press brake rear stops from geometry.
    Separates CALCULATED, VERIFIED, INVALID and UNKNOWN semantics:
    - Never marks estimated X/R/Z1/Z2 as physically confirmed.
    - Does not silently clamp out-of-range coordinates.
    - If backgauge geometry is unavailable, returns explicit UNKNOWN.
    """

    def __init__(self, machine: PressBrakeMachine):
        self.machine = machine

    def is_backgauge_feasible(
        self,
        shape: Any,
        bend_info: Dict[str, Any]
    ) -> bool:
        if shape is None:
            return True

        bend_length = bend_info.get("length", 100.0)
        if bend_length < 5.0 or bend_length > self.machine.bed_length_mm:
            return False

        return True

    def compute_step_backgauge(
        self,
        step_number: int,
        bend_info: Dict[str, Any],
        part_thickness: float = 2.0,
        confirmed_geometry: bool = False,
        placement_key: Optional[Tuple[Any, ...]] = None,
        placed_shape: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Calculates backgauge coordinates.
        Honest semantics:
        - If backgauge geometry/machine axis limits are exceeded: returns status='INVALID'
        - If backgauge geometry is absent / unverified: returns status='CALCULATED' or 'UNKNOWN'
        - Only returns status='VERIFIED' and geometry_confirmed=True when physical contact
          with a verified backgauge stop is confirmed.
        """
        if not confirmed_geometry and bend_info.get("geometry_confirmed"):
            confirmed_geometry = True
        bend_length = bend_info.get("length", 100.0)
        
        v_width = 12.0
        if hasattr(self.machine, 'die') and self.machine.die:
            if hasattr(self.machine.die, 'v_width_mm'):
                v_width = getattr(self.machine.die, 'v_width_mm')
            elif hasattr(self.machine.die, 'metadata') and "v_width_mm" in self.machine.die.metadata:
                v_width = float(self.machine.die.metadata["v_width_mm"])

        # Determine reference edge & dimension
        # In machine coordinate frame, V-die groove lies at Y=0 (or X=0 depending on station orientation).
        # Analytical estimated flange depth based on bend geometry
        flange_depth_estimate = max(20.0, bend_length * 0.5)
        flange_depth = flange_depth_estimate
        derived_from_shape = False

        if placed_shape is not None:
            try:
                from OCC.Core.BRepBndLib import brepbndlib
                from OCC.Core.Bnd import Bnd_Box
                bbox = Bnd_Box()
                brepbndlib.Add(placed_shape, bbox)
                if not bbox.IsVoid():
                    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
                    # In press brake coordinate convention:
                    # tooling centerline is at Y=0 (or X=0).
                    # Rear stop extends backward along the machine depth axis (negative X/Y depending on frame).
                    # Measure rear extension from tooling station reference:
                    flange_depth = max(flange_depth_estimate, abs(xmin))
                    derived_from_shape = True
            except Exception:
                pass

        x_mm = round(flange_depth + (v_width / 2.0), 2)
        r_mm = round(part_thickness * 0.5, 2)

        margin = max(25.0, bend_length * 0.2)
        z1_mm = round(-bend_length / 2.0 + margin, 2)
        z2_mm = round(bend_length / 2.0 - margin, 2)
        if (z2_mm - z1_mm) < 50.0:
            z1_mm = -25.0
            z2_mm = 25.0

        # Handle 180° workpiece flip if mounting key indicates inverted axis
        if placement_key and len(placement_key) >= 1 and placement_key[0] == -1.0:
            # Flipping part inverts lateral stop coordinates
            z1_mm, z2_mm = round(-z2_mm, 2), round(-z1_mm, 2)

        touch_point_1 = (x_mm, z1_mm, r_mm)
        touch_point_2 = (x_mm, z2_mm, r_mm)

        # Honest limit validation: DO NOT silently clamp out-of-range coordinates.
        # If machine axis limits are unknown/missing, they cannot be silently treated as verified.
        x_limit = getattr(self.machine, 'backgauge_x_max_mm', None)
        r_limit = getattr(self.machine, 'backgauge_r_max_mm', None)

        if x_limit is None or r_limit is None:
            return {
                "status": "UNKNOWN",
                "X_mm": x_mm,
                "R_mm": r_mm,
                "Z1_mm": z1_mm,
                "Z2_mm": z2_mm,
                "finger_touch_point_1": touch_point_1,
                "finger_touch_point_2": touch_point_2,
                "part_stability_score": 0.0,
                "reference_edge": "REAR_FLANGE_PARALLEL",
                "geometry_confirmed": False,
                "warning": "Machine backgauge axis limits (X/R) are undefined; reachability cannot be verified.",
                "error": "BACKGAUGE_AXIS_LIMITS_UNDEFINED"
            }

        if x_mm > x_limit or r_mm > r_limit or x_mm < 0.0 or r_mm < 0.0:
            return {
                "status": "INVALID",
                "X_mm": x_mm,
                "R_mm": r_mm,
                "Z1_mm": z1_mm,
                "Z2_mm": z2_mm,
                "finger_touch_point_1": touch_point_1,
                "finger_touch_point_2": touch_point_2,
                "part_stability_score": 0.0,
                "reference_edge": "REAR_FLANGE_PARALLEL",
                "geometry_confirmed": False,
                "warning": f"Backgauge position out of machine range (X={x_mm}>{x_limit} or R={r_mm}>{r_limit})",
                "error": "BACKGAUGE_LIMIT_EXCEEDED"
            }

        # Check physical backgauge tool presence and proven physical contact
        has_backgauge_geom = False
        physical_contact_proven = False
        if hasattr(self.machine, 'assembly') and self.machine.assembly:
            bg_tool = getattr(self.machine.assembly, 'backgauge', None)
            if bg_tool and not getattr(bg_tool, 'shape', None) is None:
                has_backgauge_geom = True
                # Physical contact verification: requires actual geometric contact check with stop face
                if placed_shape is not None and hasattr(self.machine.assembly, 'check_backgauge_contact'):
                    physical_contact_proven = self.machine.assembly.check_backgauge_contact(placed_shape, bg_tool)

        stability_score = 0.95 if (z2_mm - z1_mm) >= 50.0 else 0.60

        # Invariants P0-1 & P0-2:
        # Successful bend validation does NOT prove backgauge contact.
        # CAD backgauge shape existence does NOT prove verified contact without actual contact evidence.
        # Only mark VERIFIED if real physical contact and required axis/geometry checks are proven.
        if physical_contact_proven:
            status = "VERIFIED"
            is_confirmed = True
            warning_msg = None
        elif has_backgauge_geom:
            # Backgauge CAD model exists but physical contact simulation is not verified
            status = "UNKNOWN"
            is_confirmed = False
            warning_msg = "Backgauge CAD geometry exists, but physical contact has not been verified."
        elif confirmed_geometry:
            # Caller requested confirmation, but backgauge geometry is absent on machine
            status = "UNKNOWN"
            is_confirmed = False
            warning_msg = "Backgauge geometry is unavailable on machine assembly; physical finger contact cannot be verified."
        else:
            status = "CALCULATED"
            is_confirmed = False
            warning_msg = "Backgauge coordinates are estimated. B-Rep projection required for verified X dimension."

        return {
            "status": status,
            "X_mm": x_mm,
            "R_mm": r_mm,
            "Z1_mm": z1_mm,
            "Z2_mm": z2_mm,
            "finger_touch_point_1": touch_point_1,
            "finger_touch_point_2": touch_point_2,
            "part_stability_score": stability_score,
            "reference_edge": "REAR_FLANGE_PARALLEL",
            "geometry_confirmed": is_confirmed,
            "warning": warning_msg
        }

