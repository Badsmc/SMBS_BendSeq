"""Coupled Punch–Sheet–Die Kinematics for PressBrakeSim 2.5D (Phase D4-P1).

Provides:
- Exact 2D cross-sectional air-bending kinematics in unified Machine Space (s, z).
- Tooling profile binding (die opening V, shoulder radius Rd, die angle, V-depth,
  punch tip radius Rp, punch included angle).
- Non-linear angle–stroke relationship h(theta) derived from exact contact geometry
  of sheet outer surface tangent to die shoulder fillets and inner apex contacting punch tip.
- Continuous tracking of punch–sheet contact, die shoulder contacts, and bend-zone apex.
- Geometric clearance and collision/interference validation (punch flank collision,
  die bottoming out, die wall collision, flange support span).
- Unified symmetrical bisector workpiece placement in Machine Coordinates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from .fold_sequence import ANGLE_EPS
from .panel_kinematics import FoldState
from .workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D

# Default reference tooling parameters (standard 32 mm V-die and 30 deg punch)
DEFAULT_DIE_OPENING_V_MM = 32.0
DEFAULT_DIE_SHOULDER_RADIUS_MM = 2.0
DEFAULT_DIE_ANGLE_DEG = 85.0
DEFAULT_DIE_V_DEPTH_MM = 16.5
DEFAULT_PUNCH_TIP_RADIUS_MM = 0.8
DEFAULT_PUNCH_INCLUDED_ANGLE_DEG = 30.0
DEFAULT_SHEET_THICKNESS_MM = 1.5
DEFAULT_INNER_RADIUS_MM = 1.0


@dataclass
class CoupledAirBendingKinematics:
    """Coupled physical air-bending kinematics for a single active bend operation.

    All geometry lives in the unified 2D cross-section coordinate system:
      s: horizontal transverse axis across the tooling bed (s = -Y_m)
      z: vertical ram motion axis (z = Z_m)
    Die V-groove center is centered at s = 0.0, with top shoulder plane at z = die_top_z_mm.
    Punch moves along s = 0.0 vertically.
    """
    die_opening_v_mm: float = DEFAULT_DIE_OPENING_V_MM
    die_shoulder_radius_mm: float = DEFAULT_DIE_SHOULDER_RADIUS_MM
    die_angle_deg: float = DEFAULT_DIE_ANGLE_DEG
    die_v_depth_mm: float = DEFAULT_DIE_V_DEPTH_MM
    punch_tip_radius_mm: float = DEFAULT_PUNCH_TIP_RADIUS_MM
    punch_included_angle_deg: float = DEFAULT_PUNCH_INCLUDED_ANGLE_DEG
    sheet_thickness_mm: float = DEFAULT_SHEET_THICKNESS_MM
    sheet_inner_radius_mm: float = DEFAULT_INNER_RADIUS_MM
    target_bend_angle_deg: float = 90.0
    die_top_z_mm: float = 0.0
    tooling_status: str = "VERIFIED"

    @property
    def v_half(self) -> float:
        return 0.5 * self.die_opening_v_mm

    @property
    def outer_radius_mm(self) -> float:
        return self.sheet_inner_radius_mm + self.sheet_thickness_mm

    @property
    def contact_z_mm(self) -> float:
        """Vertical Z coordinate of punch tip when it first touches the flat sheet."""
        return self.die_top_z_mm + self.sheet_thickness_mm

    def stroke_for_angle(self, bend_angle_deg: float) -> float:
        """Calculate punch penetration stroke h (mm) into the V-die for a given bend angle.

        Derivation:
          Let beta = theta / 2 (flange inclination angle to horizontal).
          The sheet outer surface is tangent to:
            1. Bend zone outer cylinder: center (0, z_center), radius Ro = Ri + t
            2. Die shoulder fillet: center (sc, zc), radius Rd, where sc = V/2, zc = die_top_z - Rd.
          Projecting center distance along the flange normal yields:
            h(beta) = sc * tan(beta) - (Ro + Rd) * (1 - cos(beta)) / cos(beta)
        """
        mag = min(179.0, max(0.0, abs(float(bend_angle_deg))))
        if mag <= ANGLE_EPS:
            return 0.0

        beta = math.radians(mag / 2.0)
        sc = self.v_half
        ro = self.outer_radius_mm
        rd = self.die_shoulder_radius_mm
        cb = math.cos(beta)
        sb = math.sin(beta)
        if cb < 1e-6:
            cb = 1e-6

        # Exact non-linear contact penetration
        h = sc * (sb / cb) - (ro + rd) * ((1.0 - cb) / cb)
        return max(0.0, float(h))

    def angle_for_stroke(
        self,
        stroke_mm: float,
        target_angle_deg: Optional[float] = None,
    ) -> float:
        """Invert stroke_for_angle to calculate bend angle theta (deg) for a given stroke h (mm).

        Uses bounded Newton-Raphson iteration with fallback to bisection.
        """
        h_target = max(0.0, float(stroke_mm))
        sign = 1.0
        if target_angle_deg is not None:
            sign = -1.0 if target_angle_deg < 0 else 1.0
        elif self.target_bend_angle_deg < 0:
            sign = -1.0

        if h_target <= 1e-6:
            return 0.0

        sc = self.v_half
        ro = self.outer_radius_mm
        rd = self.die_shoulder_radius_mm

        # Initial guess from small-angle approximation: h ~= sc * beta
        beta = min(math.radians(85.0), max(0.0, h_target / max(1.0, sc)))

        for _ in range(15):
            cb = math.cos(beta)
            sb = math.sin(beta)
            if cb < 1e-6:
                break
            f_val = sc * (sb / cb) - (ro + rd) * ((1.0 - cb) / cb) - h_target
            if abs(f_val) < 1e-7:
                break
            # Derivative dh/d_beta = (sc - (ro + rd) * sb) / (cb^2)
            f_prime = (sc - (ro + rd) * sb) / (cb * cb)
            if abs(f_prime) < 1e-6:
                break
            delta = f_val / f_prime
            beta_new = beta - delta
            if beta_new <= 0.0 or beta_new >= math.radians(89.0):
                # Fall back to step damping
                beta = 0.5 * (beta + max(0.0, min(math.radians(85.0), beta_new)))
            else:
                beta = beta_new

        theta_deg = math.degrees(2.0 * beta)
        return sign * min(178.0, max(0.0, theta_deg))

    def ram_z_for_angle(self, bend_angle_deg: float) -> float:
        """Vertical Z coordinate of punch tip for given bend angle."""
        h = self.stroke_for_angle(bend_angle_deg)
        return self.contact_z_mm - h

    def support_plane_zm(self, bend_angle_deg: float) -> float:
        """Machine Z_m coordinate for active bend axis midpoint to maintain V-die contact."""
        mag = min(179.0, max(0.0, abs(float(bend_angle_deg))))
        h = self.stroke_for_angle(mag)
        return self.die_top_z_mm - h

    def bend_zone_position(self, bend_angle_deg: float) -> Dict[str, Tuple[float, float]]:
        """Coordinates of inner apex, outer apex, and center of curvature in Machine (s, z)."""
        h = self.stroke_for_angle(bend_angle_deg)
        inner_z = self.contact_z_mm - h
        outer_z = self.die_top_z_mm - h
        center_z = inner_z + self.sheet_inner_radius_mm
        return {
            "inner_apex": (0.0, inner_z),
            "outer_apex": (0.0, outer_z),
            "arc_center": (0.0, center_z),
        }

    def die_shoulder_contacts(
        self, bend_angle_deg: float
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Coordinates of sheet contact points on left and right die shoulder fillets."""
        mag = min(179.0, max(0.0, abs(float(bend_angle_deg))))
        beta = math.radians(mag / 2.0)
        sc = self.v_half
        rd = self.die_shoulder_radius_mm

        s_cont = sc - rd * math.sin(beta)
        z_cont = self.die_top_z_mm - rd * (1.0 - math.cos(beta))
        return (-s_cont, z_cont), (+s_cont, z_cont)

    def punch_clearance(self, bend_angle_deg: float, ram_z_mm: float) -> float:
        """Distance between punch tip and inner sheet surface at active bend apex."""
        h = self.stroke_for_angle(bend_angle_deg)
        inner_z = self.contact_z_mm - h
        return max(0.0, float(ram_z_mm) - inner_z)

    def validate_geometric_constraints(
        self,
        bend_angle_deg: float,
        ram_z_mm: float,
        flange_min_len_mm: float = 50.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Run geometric checks for penetration, tooling collision and unsupported span.

        Returns:
            (is_valid, status_message, details_dict)
        """
        mag = min(179.0, max(0.0, abs(float(bend_angle_deg))))
        h = self.stroke_for_angle(bend_angle_deg)
        inner_z = self.contact_z_mm - h
        included_sheet_deg = 180.0 - mag

        details = {
            "bend_angle_deg": float(bend_angle_deg),
            "ram_z_mm": float(ram_z_mm),
            "stroke_mm": float(h),
            "inner_apex_z": float(inner_z),
            "punch_clearance_mm": max(0.0, float(ram_z_mm) - inner_z),
            "included_sheet_angle_deg": float(included_sheet_deg),
            "die_v_depth_mm": float(self.die_v_depth_mm),
            "tooling_status": self.tooling_status,
        }

        # 1. Punch penetration past inner surface of sheet
        if float(ram_z_mm) < inner_z - 0.05:
            msg = (
                f"PUNCH_PENETRATION: punch tip (Z={ram_z_mm:.2f}) penetrated "
                f"past sheet inner surface (Z={inner_z:.2f})"
            )
            return False, msg, details

        # 2. Punch flank interference (sheet included angle smaller than punch tip angle)
        if included_sheet_deg < self.punch_included_angle_deg - 0.1:
            msg = (
                f"PUNCH_FLANK_COLLISION: sheet angle {included_sheet_deg:.1f}° "
                f"< punch included angle {self.punch_included_angle_deg:.1f}°"
            )
            return False, msg, details

        # 3. Die bottoming out (penetration exceeds V-groove depth)
        if h > self.die_v_depth_mm + 0.05:
            msg = (
                f"DIE_BOTTOMING_OUT: penetration {h:.2f}mm "
                f"> V-groove depth {self.die_v_depth_mm:.2f}mm"
            )
            return False, msg, details

        # 4. Die wall interference (sheet included angle smaller than die V-angle)
        if included_sheet_deg < self.die_angle_deg - 0.1:
            msg = (
                f"DIE_WALL_COLLISION: sheet angle {included_sheet_deg:.1f}° "
                f"< die V-angle {self.die_angle_deg:.1f}°"
            )
            return False, msg, details

        # 5. Unsupported flange slip into V-opening
        if flange_min_len_mm > 0.0 and flange_min_len_mm < self.v_half:
            msg = (
                f"UNSUPPORTED_FLANGE: flange length {flange_min_len_mm:.1f}mm "
                f"< half die opening {self.v_half:.1f}mm"
            )
            return False, msg, details

        return True, "GEOMETRIC_COUPLING_VERIFIED", details

    @classmethod
    def from_tooling_and_fold_state(
        cls,
        tooling_setup: Any,
        fold_state: FoldState,
        active_bend_id: str,
    ) -> "CoupledAirBendingKinematics":
        """Construct kinematics bound to selected tooling and active sheet bend."""
        # Stage 2: loaded datums are not proof of complete geometry.
        # Preserve estimates for preview, but never label them VERIFIED.
        def _positive_datum(source, name):
            value = getattr(source, name, None)
            return (float(value) if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value)) and float(value) > 0.0
                    else None)

        thickness = DEFAULT_SHEET_THICKNESS_MM
        inner_r = DEFAULT_INNER_RADIUS_MM
        target_ang = 90.0
        sheet_verified = False
        if active_bend_id in fold_state.bends:
            bend = fold_state.bends[active_bend_id]
            target_ang = float(bend.angle_deg)
            zone = getattr(bend, "bend_zone", None)
            if zone is not None:
                thick = _positive_datum(zone, "thickness_mm")
                inner = _positive_datum(zone, "inner_radius_mm")
                if thick is not None:
                    thickness = thick
                if inner is not None:
                    inner_r = inner
                sheet_verified = thick is not None and inner is not None

        v_opening = DEFAULT_DIE_OPENING_V_MM
        shoulder_r = DEFAULT_DIE_SHOULDER_RADIUS_MM
        die_angle = DEFAULT_DIE_ANGLE_DEG
        v_depth = DEFAULT_DIE_V_DEPTH_MM
        die_top_z = 0.0
        die_verified = False

        die = getattr(tooling_setup, "die", None)
        dd = getattr(die, "die_datums", None)
        if dd is not None:
            vals = {
                "v_opening_mm": _positive_datum(dd, "v_opening_mm"),
                "shoulder_radius_mm": _positive_datum(dd, "shoulder_radius_mm"),
                "die_angle_deg": _positive_datum(dd, "die_angle_deg"),
                "v_depth_mm": _positive_datum(dd, "v_depth_mm"),
            }
            if vals["v_opening_mm"] is not None:
                v_opening = vals["v_opening_mm"]
            if vals["shoulder_radius_mm"] is not None:
                shoulder_r = vals["shoulder_radius_mm"]
            if vals["die_angle_deg"] is not None:
                die_angle = vals["die_angle_deg"]
            if vals["v_depth_mm"] is not None:
                v_depth = vals["v_depth_mm"]
            die_top = getattr(tooling_setup, "sheet_bottom_z_mm", None)
            die_top_valid = (isinstance(die_top, (int, float))
                             and not isinstance(die_top, bool)
                             and math.isfinite(float(die_top)))
            if die_top_valid:
                die_top_z = float(die_top)
            die_verified = all(v is not None for v in vals.values()) and die_top_valid

        punch_r = DEFAULT_PUNCH_TIP_RADIUS_MM
        punch_angle = DEFAULT_PUNCH_INCLUDED_ANGLE_DEG
        punch_verified = False

        punch = getattr(tooling_setup, "punch", None)
        pd = getattr(punch, "punch_datums", None)
        if pd is not None:
            rp = _positive_datum(pd, "tip_radius_mm")
            angle = _positive_datum(pd, "included_angle_deg")
            if rp is not None:
                punch_r = rp
            if angle is not None:
                punch_angle = angle
            punch_verified = rp is not None and angle is not None

        status = ("VERIFIED" if sheet_verified and die_verified and punch_verified
                  else "TOOLING_GEOMETRY_DEFAULT_ESTIMATE"
                  if dd is not None or pd is not None
                  else "TOOLING_GEOMETRY_NOT_SPECIFIED")

        return cls(
            die_opening_v_mm=v_opening,
            die_shoulder_radius_mm=shoulder_r,
            die_angle_deg=die_angle,
            die_v_depth_mm=v_depth,
            punch_tip_radius_mm=punch_r,
            punch_included_angle_deg=punch_angle,
            sheet_thickness_mm=thickness,
            sheet_inner_radius_mm=inner_r,
            target_bend_angle_deg=target_ang,
            die_top_z_mm=die_top_z,
            tooling_status=status,
        )

