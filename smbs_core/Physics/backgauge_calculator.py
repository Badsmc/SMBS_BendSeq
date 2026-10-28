"""
backgauge_calculator.py - Hybrid Strategy for Backgauge / Part Basing Calculation
"""

import math
from typing import Dict, Any, Tuple, Optional
from .machine_kinematics import PressBrakeMachine


class BackgaugeCalculator:
    """
    Calculates backgauge positions (X, R, Z1, Z2 axes) for press brake rear stops from geometry.
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
        confirmed_geometry: bool = False
    ) -> Dict[str, Any]:
        bend_length = bend_info.get("length", 100.0)
        
        v_width = 12.0
        if hasattr(self.machine, 'die') and self.machine.die:
            if hasattr(self.machine.die, 'v_width_mm'):
                v_width = getattr(self.machine.die, 'v_width_mm')
            elif hasattr(self.machine.die, 'metadata') and "v_width_mm" in self.machine.die.metadata:
                v_width = float(self.machine.die.metadata["v_width_mm"])

        flange_depth_estimate = max(20.0, bend_length * 0.5)
        x_mm = round(flange_depth_estimate + (v_width / 2.0), 2)
        x_mm = min(x_mm, self.machine.backgauge_x_max_mm)

        r_mm = round(part_thickness * 0.5, 2)
        r_mm = min(r_mm, self.machine.backgauge_r_max_mm)

        margin = max(25.0, bend_length * 0.2)
        z1_mm = round(-bend_length / 2.0 + margin, 2)
        z2_mm = round(bend_length / 2.0 - margin, 2)
        if (z2_mm - z1_mm) < 50.0:
            z1_mm = -25.0
            z2_mm = 25.0

        touch_point_1 = (x_mm, z1_mm, r_mm)
        touch_point_2 = (x_mm, z2_mm, r_mm)

        stability_score = 0.95 if (z2_mm - z1_mm) >= 50.0 else 0.60
        is_confirmed = confirmed_geometry or bend_info.get("geometry_confirmed", False)

        warning_msg = (
            None if is_confirmed
            else "Backgauge coordinates are estimated. B-Rep projection required for verified X dimension."
        )

        return {
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
