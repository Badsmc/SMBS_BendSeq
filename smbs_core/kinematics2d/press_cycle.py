"""Headless Press Brake Working Cycle & Ram Motion Controller (Phase 5B).

Implements a deterministic, Qt- and FreeCAD-independent state machine for the
2.5D press brake working cycle:
  IDLE -> APPROACH -> CONTACT -> BENDING -> BOTTOM -> RETRACT -> STEP_COMPLETE -> COMPLETED

Architectural contract:
1. `BendSequenceController` owns operation order (`bend_order`) and the active
   operation index (`active_index`).
2. `PressCycleController` owns the intra-operation phases (`APPROACH`, `CONTACT`,
   `BENDING`, `BOTTOM`, `RETRACT`, `STEP_COMPLETE`) and punch ram position
   (`ram_z_mm`, `ram_stroke_mm`) in Machine coordinates `(s, z)`.
3. Authoritative single-writer angle update: during `FULL_CYCLE` playback,
   `PressCycleController` updates the active bend angle exclusively in the
   `BENDING` (and `BOTTOM` clamp) phase via `fold_state.set_angle(...)`, while
   `BendSequenceController.advance_time()` is NOT called simultaneously.
4. Phase 5B is an explicit `KINEMATIC_PREVIEW` (never `PHYSICALLY_VALIDATED`):
   it does NOT claim physical contact mechanics, tonnage calculation, elastic
   deflection, springback, or manufacturability verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Callable, Dict, Optional

from .fold_sequence import ANGLE_EPS, BendSequenceController, PlaybackStatus, SequenceError
from .workpiece_placement_3d import PlacementTransform3D, WorkpiecePlacement3D
from .workpiece_transfer_3d import Transfer3D, build_bend_transfer
from .coupled_kinematics_2d import CoupledAirBendingKinematics
from .ram_cycle_geometry import ram_cycle_positions
from .tooling_25d import (
    DEFAULT_INITIAL_PUNCH_CLEARANCE_MM,
    NOT_SPECIFIED,
    ToolingError,
    ToolingSetup2D,
    ValidationStatus,
)


class PressCycleError(ValueError):
    """Raised when a press cycle configuration or transition violates the Phase 5B contract."""


class CycleSimulationMode(str, Enum):
    """Simulation fidelity mode. Phase 5B supports ONLY `KINEMATIC_PREVIEW`."""
    KINEMATIC_PREVIEW = "KINEMATIC_PREVIEW"


class CycleExecutionMode(str, Enum):
    """User-selectable playback mode in PressBrakeSim 2.5D."""
    BEND_ONLY = "BEND_ONLY"
    FULL_CYCLE = "FULL_CYCLE"


class CyclePhase(str, Enum):
    """Stages of a press brake working cycle for a single bend operation."""
    IDLE = "IDLE"
    LOAD_TO_GAUGE = "LOAD_TO_GAUGE"
    APPROACH = "APPROACH"
    CONTACT = "CONTACT"
    BENDING = "BENDING"
    BOTTOM = "BOTTOM"
    RETRACT = "RETRACT"
    STEP_COMPLETE = "STEP_COMPLETE"
    TRANSFER = "TRANSFER"  # visual machine repositioning, no new bend angles
    COMPLETED = "COMPLETED"


class ContactBoundaryKind(str, Enum):
    """Explicitly distinguishes geometric upper-surface contact from assumed boundary."""
    GEOMETRIC_CONTACT = "GEOMETRIC_CONTACT"
    ASSUMED_CONTACT = "ASSUMED_CONTACT"


class WorkpieceOrientationStatus(str, Enum):
    """Explicitly tracks how the sheet orientation relates to the punch/die setup."""
    STANDARD_UP_BEND = "STANDARD_UP_BEND"
    ASSUMED_WORKPIECE_FLIP_FOR_OPPOSITE_BEND = "ASSUMED_WORKPIECE_FLIP_FOR_OPPOSITE_BEND"


# Explicit valid parameter ranges (mm, s, deg/s)
MIN_SPEED_MM_S = 0.5
MAX_SPEED_MM_S = 500.0
MIN_BEND_DURATION_S = 0.05
MAX_BEND_DURATION_S = 120.0
MIN_BEND_RATE_DEG_S = 1.0
MAX_BEND_RATE_DEG_S = 720.0
MIN_CLEARANCE_MM = 0.5
MAX_CLEARANCE_MM = 200.0
MIN_PREVIEW_STROKE_MM = 0.5
MAX_PREVIEW_STROKE_MM = 150.0
MIN_BOTTOM_DWELL_S = 0.0
MAX_BOTTOM_DWELL_S = 30.0

# Kinematic limits for geometric transfer preview (spatial interpolation only;
# NOT certified machine axes speed or collision-safe trajectory limits).
GEOMETRIC_PREVIEW_TRANSFER_MAX_LINEAR_MM_S = 250.0
GEOMETRIC_PREVIEW_TRANSFER_MAX_ANGULAR_DEG_S = 90.0


@dataclass(frozen=True)
class CycleConfig:
    """User-configurable parameters for the kinematic press brake working cycle.

    All parameters have explicit physical units (mm, s, deg/s) and validated bounds.
    None of these parameters claim to represent a physical machine controller or
    contact-mechanics solver; `simulation_mode` is fixed to `KINEMATIC_PREVIEW`.
    """
    approach_speed_mm_s: float = 25.0
    retract_speed_mm_s: float = 35.0
    bend_rate_deg_s: float = 90.0
    bend_duration_s: Optional[float] = None
    initial_clearance_mm: float = DEFAULT_INITIAL_PUNCH_CLEARANCE_MM
    preview_stroke_mm: float = 4.0
    bottom_dwell_s: float = 0.10
    simulation_mode: CycleSimulationMode = CycleSimulationMode.KINEMATIC_PREVIEW

    def __post_init__(self) -> None:
        if self.simulation_mode != CycleSimulationMode.KINEMATIC_PREVIEW:
            raise PressCycleError(
                f"Unsupported simulation_mode {self.simulation_mode!r}: "
                "Phase 5B supports only CycleSimulationMode.KINEMATIC_PREVIEW "
                "(never PHYSICALLY_VALIDATED)."
            )
        _check_range("approach_speed_mm_s", self.approach_speed_mm_s, MIN_SPEED_MM_S, MAX_SPEED_MM_S, "mm/s")
        _check_range("retract_speed_mm_s", self.retract_speed_mm_s, MIN_SPEED_MM_S, MAX_SPEED_MM_S, "mm/s")
        _check_range("bend_rate_deg_s", self.bend_rate_deg_s, MIN_BEND_RATE_DEG_S, MAX_BEND_RATE_DEG_S, "deg/s")
        if self.bend_duration_s is not None:
            _check_range("bend_duration_s", self.bend_duration_s, MIN_BEND_DURATION_S, MAX_BEND_DURATION_S, "s")
        _check_range("initial_clearance_mm", self.initial_clearance_mm, MIN_CLEARANCE_MM, MAX_CLEARANCE_MM, "mm")
        _check_range("preview_stroke_mm", self.preview_stroke_mm, MIN_PREVIEW_STROKE_MM, MAX_PREVIEW_STROKE_MM, "mm")
        _check_range("bottom_dwell_s", self.bottom_dwell_s, MIN_BOTTOM_DWELL_S, MAX_BOTTOM_DWELL_S, "s")

    def effective_bend_duration_s(self, target_angle_deg: float) -> float:
        """Return total bending phase duration (s) for a given target angle (deg)."""
        if self.bend_duration_s is not None:
            return float(self.bend_duration_s)
        mag = abs(float(target_angle_deg))
        if mag <= ANGLE_EPS:
            return MIN_BEND_DURATION_S
        return max(MIN_BEND_DURATION_S, mag / float(self.bend_rate_deg_s))


def _check_range(name: str, val: Any, low: float, high: float, unit: str) -> None:
    try:
        fval = float(val)
    except (TypeError, ValueError) as exc:
        raise PressCycleError(f"{name} must be a finite number ({unit}), got {val!r}") from exc
    if not math.isfinite(fval) or not (low <= fval <= high):
        raise PressCycleError(
            f"{name} must be within [{low}, {high}] {unit}, got {fval!r}"
        )


class PressCycleController:
    """Headless state machine for the Phase 5B Press Brake Working Cycle & Ram Motion.

    Coordinates with `BendSequenceController` (which owns `order` and `active_index`)
    and `ToolingSetup2D` (which owns the selected punch/die and Machine transforms)
    without duplicating `FoldState.angles`.
    """

    def __init__(
        self,
        sequence_controller: BendSequenceController,
        tooling_setup: ToolingSetup2D,
        config: Optional[CycleConfig] = None,
        cross_section_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        follow_sheet_contact_preview: bool = False,
        animate_workpiece_transfer: bool = False,
    ) -> None:
        if not isinstance(sequence_controller, BendSequenceController):
            raise PressCycleError("sequence_controller must be a BendSequenceController instance")
        if not isinstance(tooling_setup, ToolingSetup2D):
            raise PressCycleError("tooling_setup must be a ToolingSetup2D instance")

        self.sequence: BendSequenceController = sequence_controller
        self.tooling: ToolingSetup2D = tooling_setup
        self.config: CycleConfig = config or CycleConfig()
        self.execution_mode: CycleExecutionMode = CycleExecutionMode.FULL_CYCLE
        self._cross_section_provider = cross_section_provider
        # Opt-in GUI-only geometric contact preview; existing headless
        # programmable-stroke controller tests retain their previous contract.
        self.follow_sheet_contact_preview = bool(follow_sheet_contact_preview)
        # Repositioning is opt-in and purely kinematic. One existing controller
        # and playback timer own every movement; no separate GUI animation.
        self.animate_workpiece_transfer = bool(animate_workpiece_transfer)
        # Visual lead-in for the first operation; opt-in from gauge settings.
        # A real machine feed path/contact is NOT verified in this preview.
        self.initial_feed_preview_mm = 0.0
        self.initial_feed_progress = 0.0
        self.initial_feed_preview_speed_mm_s = 40.0
        self.transfer_progress = 0.0
        self.transfer_duration_s = 1.8
        self.transfer_feed_mm = 0.0
        self.transfer_target_feed_mm = 0.0
        self.transfer_lift_mm = 0.0
        self.transfer_rotation_deg = 0.0
        self.transfer_3d: Optional[Transfer3D] = None
        self.transfer_fault: Optional[str] = None
        self.contact_tracking_status = "PROGRAMMED_PREVIEW_STROKE"
        self.coupled_kinematics: Optional[CoupledAirBendingKinematics] = None
        # A user-controlled slider scrub is not an automatic press phase.
        # Keep it explicitly distinguishable from APPROACH/IDLE/RETRACT.
        self._manual_preview_active = False

        self.phase: CyclePhase = CyclePhase.IDLE
        self.contact_kind: ContactBoundaryKind = ContactBoundaryKind.ASSUMED_CONTACT
        self.orientation_status: WorkpieceOrientationStatus = WorkpieceOrientationStatus.STANDARD_UP_BEND

        # Machine Z coordinates (mm) for the active operation
        self.sheet_top_z_mm: float = 0.0
        self.sheet_bottom_z_mm: float = -1.5
        self.upper_z_mm: float = self.sheet_top_z_mm + self.config.initial_clearance_mm
        self.contact_z_mm: float = self.sheet_top_z_mm
        self.bottom_z_mm: float = self.contact_z_mm - self.config.preview_stroke_mm
        self.ram_z_mm: float = self.upper_z_mm
        self.ram_stroke_mm: float = 0.0
        self.dwell_remaining_s: float = 0.0

        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)

    @property
    def simulation_mode(self) -> CycleSimulationMode:
        return self.config.simulation_mode

    @property
    def status(self) -> PlaybackStatus:
        return self.sequence.status

    @property
    def active_bend_id(self) -> str:
        return self.sequence.active_bend_id

    @property
    def active_index(self) -> int:
        return self.sequence.active_index

    @property
    def total_steps(self) -> int:
        return self.sequence.total_steps

    @property
    def current_angle_deg(self) -> float:
        return self.sequence.current_angle_deg

    @property
    def target_angle_deg(self) -> float:
        return self.sequence.target_angle_deg

    def set_config(self, config: CycleConfig) -> None:
        if not isinstance(config, CycleConfig):
            raise PressCycleError("config must be a CycleConfig instance")
        self.config = config
        self.sequence.speed_deg_per_sec = float(config.bend_rate_deg_s)
        if self.phase in (CyclePhase.IDLE, CyclePhase.COMPLETED):
            self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)

    def set_execution_mode(self, mode: CycleExecutionMode | str) -> None:
        resolved = CycleExecutionMode(mode) if isinstance(mode, str) else mode
        if resolved not in (CycleExecutionMode.BEND_ONLY, CycleExecutionMode.FULL_CYCLE):
            raise PressCycleError(f"Unsupported execution mode: {mode!r}")
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.pause()
        self.execution_mode = resolved
        if resolved == CycleExecutionMode.BEND_ONLY:
            # A user scrubbing an angle must not see the punch snap up while
            # the sheet remains at the coupled die-penetration depth.
            if self._manual_preview_active:
                self.preview_punch_at_current_angle()
            else:
                self._park_punch_at_upper_position()

    def _current_cross_section(self) -> Optional[Dict[str, Any]]:
        if self._cross_section_provider is not None:
            try:
                return self._cross_section_provider()
            except Exception:
                return None
        return None

    def _evaluate_contact_kind(self, sec: Optional[Dict[str, Any]]) -> ContactBoundaryKind:
        """Determine whether the contact boundary is `GEOMETRIC_CONTACT` or `ASSUMED_CONTACT`.

        `GEOMETRIC_CONTACT` holds only when:
          1. A verified Punch (`GEOMETRY_VERIFIED`) with a known working tip datum is selected;
          2. A verified Die (`GEOMETRY_VERIFIED`) with a known V-groove center datum is selected;
          3. A valid sheet cross-section with explicit `zone_polygon` and `neutral_curve` is present.
        Otherwise, the boundary is explicitly marked `ASSUMED_CONTACT`.
        """
        punch_ok = (
            self.tooling.punch is not None
            and self.tooling.punch.validation_status == ValidationStatus.GEOMETRY_VERIFIED
            and self.tooling.punch.punch_datums is not None
            and self.tooling.punch.working_datum != NOT_SPECIFIED
        )
        die_ok = (
            self.tooling.die is not None
            and self.tooling.die.validation_status == ValidationStatus.GEOMETRY_VERIFIED
            and self.tooling.die.die_datums is not None
            and self.tooling.die.working_datum != NOT_SPECIFIED
        )
        sec_ok = bool(sec and sec.get("neutral_curve") and sec.get("zone_polygon"))
        if punch_ok and die_ok and sec_ok:
            return ContactBoundaryKind.GEOMETRIC_CONTACT
        return ContactBoundaryKind.ASSUMED_CONTACT

    def _evaluate_orientation_status(self) -> WorkpieceOrientationStatus:
        """Determine workpiece orientation status for the active bend sign.

        In PressBrakeSim 2.5D Machine coordinates, the punch is above the sheet (+Z)
        and moves downward (-Z) into the lower die. For a negative target angle
        (e.g. -90 deg or -45 deg on a Z-part), physical press brake operation
        requires flipping/re-orienting the workpiece between bends. Because Phase 5B
        keeps the root reference frame and does not physically flip the part in the
        die, negative bends are explicitly marked `ASSUMED_WORKPIECE_FLIP_FOR_OPPOSITE_BEND`.
        """
        if self.target_angle_deg < -ANGLE_EPS:
            return WorkpieceOrientationStatus.ASSUMED_WORKPIECE_FLIP_FOR_OPPOSITE_BEND
        return WorkpieceOrientationStatus.STANDARD_UP_BEND

    def _sync_bounds_and_orientation_for_active_bend(self, reposition_tools: bool = True) -> None:
        """Compute `upper_z_mm`, `contact_z_mm`, `bottom_z_mm` for the active bend
        and optionally align the static die + retracted punch to the active bend line.
        """
        sec = self._current_cross_section()
        self.contact_kind = self._evaluate_contact_kind(sec)
        self.orientation_status = self._evaluate_orientation_status()

        # D4-P1: Couple active operation to physical air-bending kinematics
        self.coupled_kinematics = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            self.tooling, self.sequence.fold_state, self.active_bend_id
        )

        self.sheet_bottom_z_mm = self.coupled_kinematics.die_top_z_mm
        self.sheet_top_z_mm = self.coupled_kinematics.contact_z_mm
        self.contact_z_mm = self.sheet_top_z_mm
        target_mag = abs(self.target_angle_deg)
        max_stroke = self.coupled_kinematics.stroke_for_angle(target_mag)
        ram_positions = ram_cycle_positions(
            self.contact_z_mm, self.contact_z_mm - max_stroke,
            self.config.initial_clearance_mm,
        )
        self.upper_z_mm = ram_positions["retract_end_z_mm"]
        self.bottom_z_mm = ram_positions["bottom_z_mm"]
        self.contact_tracking_status = f"COUPLED_AIR_BENDING ({self.coupled_kinematics.tooling_status})"

        if reposition_tools:
            self.ram_z_mm = self.upper_z_mm
            self.ram_stroke_mm = 0.0
            active_bz = None
            try:
                active_bz = self.sequence.active_bend.bend_zone
            except Exception:
                active_bz = None
            if self.tooling.punch is not None or self.tooling.die is not None:
                try:
                    self.tooling.align_to_cross_section(
                        sec,
                        clearance_mm=self.config.initial_clearance_mm,
                        bend_id=self.active_bend_id,
                        bend_zone=active_bz,
                    )
                except ToolingError:
                    # Tooling may have NEEDS_ALIGNMENT or no datums yet; still update ram_z_mm
                    self.tooling.set_punch_ram_z(self.ram_z_mm, stroke_mm=0.0)
            else:
                self.tooling.set_punch_ram_z(self.ram_z_mm, stroke_mm=0.0)

    def _rebase_contact_to_die_supported_sheet(self, sec) -> None:
        """Reference ram travel to sheet actually positioned on V shoulders."""
        if (self.execution_mode != CycleExecutionMode.FULL_CYCLE
                or not sec or not sec.get("zone_polygon")
                or not self.tooling.aligned or self.tooling.die is None):
            return
        try:
            from .workpiece_pose_2d import (
                machine_pose_for_cycle, upper_zone_at_machine_axis
            )
            pose = machine_pose_for_cycle(sec, self, self.tooling)
            heights = [z for _s, z in pose.points(sec["zone_polygon"])]
            axis = self.tooling.die_v_center_machine()
            if axis is None:
                return
            self.sheet_top_z_mm = upper_zone_at_machine_axis(sec, pose, axis[0])
            self.sheet_bottom_z_mm = min(heights)
            self.contact_z_mm = self.sheet_top_z_mm
            self.upper_z_mm = self.contact_z_mm + float(self.config.initial_clearance_mm)
            self.bottom_z_mm = self.contact_z_mm - float(self.config.preview_stroke_mm)
            self._set_punch_z(self.upper_z_mm)
        except (ValueError, AttributeError, TypeError):
            # The renderer explicitly flags unsupported V-die contact.
            pass

    def _set_punch_from_current_bend_surface(self, fallback_z_mm: float) -> None:
        """Place the punch tip at the active zone upper surface in preview.

        Uses the actual machine-space sheet support and bend-axis cross-section.
        This is *only* geometric preview alignment, not collision/force physics.
        """
        if not (self.follow_sheet_contact_preview and self.tooling.aligned
                and self.tooling.punch is not None and self.tooling.die is not None):
            self.contact_tracking_status = "PROGRAMMED_PREVIEW_STROKE"
            self._set_punch_z(fallback_z_mm)
            return
        try:
            from .workpiece_pose_2d import (
                machine_pose_for_cycle, upper_zone_at_machine_axis
            )
            sec = self._current_cross_section()
            if sec is None:
                raise PressCycleError("Missing active bend section")
            pose = machine_pose_for_cycle(sec, self, self.tooling)
            axis = self.tooling.die_v_center_machine()
            if axis is None:
                raise PressCycleError("Missing die V center")
            top_z = upper_zone_at_machine_axis(sec, pose, axis[0])
            if not math.isfinite(top_z):
                raise PressCycleError("Nonfinite tracked punch coordinate")
            self.contact_tracking_status = "GEOMETRIC_ZONE_TIP_PREVIEW"
            self._set_punch_z(top_z)
        except (ValueError, TypeError, AttributeError) as exc:
            self.contact_tracking_status = "ASSUMED_STROKE: {}".format(exc)
            self._set_punch_z(fallback_z_mm)

    def _park_punch_at_upper_position(self) -> None:
        self.ram_z_mm = self.upper_z_mm
        self.ram_stroke_mm = 0.0
        self.tooling.set_punch_ram_z(self.ram_z_mm, stroke_mm=0.0)

    def _set_punch_z(self, z_mm: float) -> None:
        self.ram_z_mm = float(z_mm)
        self.ram_stroke_mm = max(0.0, self.contact_z_mm - self.ram_z_mm)
        self.tooling.set_punch_ram_z(self.ram_z_mm, stroke_mm=self.ram_stroke_mm)

    def on_tooling_changed(self) -> None:
        """Recompute current tool bounds without desynchronizing slider preview."""
        manual_scrub = self._manual_preview_active
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
        if manual_scrub:
            self.preview_punch_at_current_angle()

    def reset(self) -> None:
        """Reset all bends to 0 deg, return punch to upper retracted position,
        set phase to IDLE, and set active operation to the first bend in `bend_order`.
        Preserves user-defined `bend_order`.
        """
        self._manual_preview_active = False
        self.sequence.reset()
        self.phase = CyclePhase.IDLE
        self.dwell_remaining_s = 0.0
        self.initial_feed_progress = 0.0
        self.transfer_progress = 0.0
        self.transfer_3d = None
        self.transfer_fault = None
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)

    def select_operation(self, index: int) -> None:
        """Select operation `index` manually; parks punch at upper position."""
        self._manual_preview_active = False
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.pause()
        self.sequence.select_operation(index)
        self.phase = (
            CyclePhase.COMPLETED
            if self.sequence.status == PlaybackStatus.COMPLETED
            else CyclePhase.IDLE
        )
        self.dwell_remaining_s = 0.0
        self.transfer_progress = 0.0
        self.transfer_3d = None
        self.transfer_fault = None
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)

    def set_manual_angle(self, signed_angle_deg: float) -> None:
        """Manual slider/step angle control.

        Never triggers automatic cycle motion; parks the punch at the safe upper
        retracted preview position so the two modes never fight over angle or ram Z.
        """
        self._manual_preview_active = False
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.pause()
        self.sequence.set_manual_angle(signed_angle_deg)
        self.dwell_remaining_s = 0.0
        if self.sequence.status == PlaybackStatus.COMPLETED:
            self.phase = CyclePhase.COMPLETED
        else:
            self.phase = CyclePhase.IDLE
        self._park_punch_at_upper_position()

    def preview_punch_at_current_angle(self) -> None:
        """Couple the visible punch to the current bend in UNVERIFIED preview.

        The authoritative coupled air-bending model supplies the punch stroke
        for the *actual* FoldState angle; no invented linear interpolation or
        collision claim. The die and source geometry remain unchanged. This is
        exclusively visual kinematics and never invokes a physical validator.
        """
        if self.coupled_kinematics is None:
            raise PressCycleError("No coupled bend/punch kinematics available")
        angle = abs(float(self.current_angle_deg))
        target = abs(float(self.target_angle_deg))
        if not math.isfinite(angle) or not math.isfinite(target):
            raise PressCycleError("Nonfinite bend angle in punch preview")
        if angle > target + 1e-4:
            raise PressCycleError("Preview angle exceeds the selected bend target")
        stroke = float(self.coupled_kinematics.stroke_for_angle(
            min(angle, target)
        ))
        if not math.isfinite(stroke) or stroke < -1e-6:
            raise PressCycleError("Invalid coupled punch stroke")
        self._set_punch_z(self.contact_z_mm - max(0.0, stroke))
        self.contact_tracking_status = "UNVERIFIED_SLIDER_KINEMATIC_PREVIEW"

    def preview_workpiece_support_zm(self) -> float:
        """Shared V-die sheet position from the same coupled stroke as PUNCH.

        WorkpiecePlacement3D maps the active bend arc center to this height.
        The bend zone outer radius is authoritative (SheetMetal); neither the
        object placement nor the lower tool is modified. Preview only:
        NO collision, die-support or force validation is implied.
        """
        bend = self.sequence.fold_state.bends[self.active_bend_id]
        zone = bend.bend_zone
        if zone is None:
            raise PressCycleError(
                "Manual preview requires an authoritative BendZone"
            )
        if self.coupled_kinematics is None:
            raise PressCycleError(
                "Manual preview requires coupled punch-sheet kinematics"
            )
        outer_r = float(zone.outer_radius_mm)
        if not math.isfinite(outer_r) or outer_r <= 0.0:
            raise PressCycleError("Nonfinite/invalid bend zone outer radius")
        stroke = float(
            self.coupled_kinematics.stroke_for_angle(
                abs(self.current_angle_deg)
            )
        )
        if not math.isfinite(stroke) or stroke < -1e-6:
            raise PressCycleError("Nonfinite/invalid workpiece stroke")
        return outer_r - max(0.0, stroke)

    def set_manual_preview_angle(self, signed_angle_deg: float) -> None:
        """Set slider angle, coupled punch AND matching workpiece preview.

        No automatic phase is started. The placement renderer consumes the
        preview flag to use the shared support height even when phase=IDLE.
        The legacy set_manual_angle API still retracts the ram for callers.
        """
        self.set_manual_angle(signed_angle_deg)
        self.preview_punch_at_current_angle()
        self._manual_preview_active = True

    def next_step(self) -> None:
        """Step instantaneously to the next completed bend state with punch retracted at upper Z."""
        self._manual_preview_active = False
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.pause()
        self.sequence.next_step()
        self.dwell_remaining_s = 0.0
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
        self._park_punch_at_upper_position()
        self.phase = (
            CyclePhase.COMPLETED
            if self.sequence.status == PlaybackStatus.COMPLETED
            else CyclePhase.IDLE
        )

    def previous_step(self) -> None:
        """Step instantaneously to the previous completed bend state with punch retracted at upper Z."""
        self._manual_preview_active = False
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.pause()
        self.sequence.previous_step()
        self.dwell_remaining_s = 0.0
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
        self._park_punch_at_upper_position()
        self.phase = CyclePhase.IDLE

    def play_all(self) -> None:
        """Start or restart automatic playback in the active `execution_mode`."""
        self._manual_preview_active = False
        if self.execution_mode == CycleExecutionMode.BEND_ONLY:
            self.sequence.play_all()
            self.phase = CyclePhase.BENDING
            self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
            return

        # FULL_CYCLE mode:
        was_completed = self.phase == CyclePhase.COMPLETED or (
            self.phase == CyclePhase.IDLE
            and (
                self.sequence.status == PlaybackStatus.COMPLETED
                or all(self.sequence.is_bend_completed(bid) for bid in self.sequence.order)
            )
        )
        was_paused_mid_cycle = (
            self.sequence.status == PlaybackStatus.PAUSED
            and self.phase in (
                CyclePhase.LOAD_TO_GAUGE,
                CyclePhase.APPROACH,
                CyclePhase.CONTACT,
                CyclePhase.BENDING,
                CyclePhase.BOTTOM,
                CyclePhase.RETRACT,
                CyclePhase.STEP_COMPLETE,
                CyclePhase.TRANSFER,
            )
            and not was_completed
        )

        if was_paused_mid_cycle:
            # Validate sequential prefix (or allow RETRACT/BOTTOM/STEP_COMPLETE where active bend just reached target)
            if self.phase in (CyclePhase.BOTTOM, CyclePhase.RETRACT, CyclePhase.STEP_COMPLETE, CyclePhase.TRANSFER):
                self.sequence.status = PlaybackStatus.PLAYING
                return
            self.sequence.play_all()
            return

        # Starting fresh from IDLE, COMPLETED, or after a manual slider change on a valid prefix
        self.sequence.play_all()
        self.dwell_remaining_s = 0.0
        self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)

        # If resuming from a partially bent active operation (e.g. after manual slider set to 30 deg on B01),
        # position the punch at the corresponding interpolated BENDING stroke and continue BENDING;
        # otherwise start at the top of APPROACH.
        cur_mag = abs(self.current_angle_deg)
        target_mag = abs(self.target_angle_deg)
        if cur_mag > ANGLE_EPS and target_mag > ANGLE_EPS and cur_mag < target_mag - ANGLE_EPS:
            frac = min(1.0, max(0.0, cur_mag / target_mag))
            self.phase = CyclePhase.BENDING
            self._set_punch_from_current_bend_surface(
                self.contact_z_mm - frac * self.config.preview_stroke_mm
            )
        else:
            if (self.initial_feed_preview_mm > 0.0
                    and self.sequence.active_index == 0
                    and not any(self.sequence.is_bend_completed(bid)
                                for bid in self.sequence.order)):
                self.initial_feed_progress = 0.0
                self.phase = CyclePhase.LOAD_TO_GAUGE
            else:
                self.phase = CyclePhase.APPROACH
            self._set_punch_z(self.upper_z_mm)

    def pause(self) -> None:
        """Pause automatic playback, freezing angle, ram position, phase, and dwell timer."""
        if self.sequence.status == PlaybackStatus.PLAYING:
            self.sequence.pause()

    def resume(self) -> None:
        """Resume automatic playback from the exact paused phase, ram position, angle, and dwell."""
        if self.sequence.status != PlaybackStatus.PAUSED:
            return
        if self.transfer_fault is not None:
            # Cannot resume after an unrecovered transfer fault without reset
            return
        if self.execution_mode == CycleExecutionMode.BEND_ONLY:
            self.sequence.resume()
            self.phase = CyclePhase.BENDING
            return
        self.play_all()

    def _begin_workpiece_transfer(self) -> None:
        """Prepare visible retract/turn/feed motion without changing bend state."""
        self.transfer_progress = 0.0
        active = self.sequence.active_bend
        next_id = self.sequence.order[self.sequence.active_index + 1]
        nxt = self.sequence.bend_for(next_id)
        zone = active.bend_zone
        next_zone = nxt.bend_zone
        if zone is None or next_zone is None:
            raise PressCycleError("Cannot transfer workpiece without both bend axes")
        a = zone.axis_xy
        b = next_zone.axis_xy
        ac = ((a[0][0]+a[1][0])/2.0, (a[0][1]+a[1][1])/2.0)
        bc = ((b[0][0]+b[1][0])/2.0, (b[0][1]+b[1][1])/2.0)
        ux, uy = zone.left_xy
        # Signed feed to bring the next actual bend axis under the same punch:
        # not a decorative retract-and-return with zero final displacement.
        self.transfer_target_feed_mm = -(
            (bc[0]-ac[0])*ux + (bc[1]-ac[1])*uy
        )
        self.transfer_feed_mm = abs(self.transfer_target_feed_mm)
        if not math.isfinite(self.transfer_target_feed_mm):
            raise PressCycleError("Invalid next-bend feed distance")
        # In handling preview, lift sufficiently to make the entire formed
        # profile visibly clear the V-die before turning it. This is not a
        # certified collision-safe transfer with the punch or machine frame.
        section = self._current_cross_section()
        outlines = []
        if section:
            outlines.extend(section.get("inner_surface", ()))
            outlines.extend(section.get("outer_surface", ()))
            for _id, poly in section.get("other_panel_polygons", ()):
                outlines.extend(poly)
        height = (
            max(p[1] for p in outlines) - min(p[1] for p in outlines)
            if outlines else 0.0
        )
        self.transfer_lift_mm = max(
            10.0,
            height + float(self.config.initial_clearance_mm),
            float(self.config.initial_clearance_mm) +
            2.0 * float(zone.thickness_mm),
        )
        old_negative = self.target_angle_deg < 0
        new_negative = nxt.angle_deg < 0
        if old_negative != new_negative:
            self.transfer_rotation_deg = -180.0 if old_negative else 180.0
        else:
            self.transfer_rotation_deg = 0.0

        # Construct authoritative 3D spatial transfer trajectory via Transfer3D
        self.transfer_fault = None
        try:
            start_pose = self.current_workpiece_placement_3d()
            next_bz = (
                self.sequence.fold_state.bends[nxt.bend_id].bend_zone
                if nxt.bend_id in self.sequence.fold_state.bends
                else None
            )
            if next_bz is not None:
                next_outer_r = next_bz.outer_radius_mm
            elif self.coupled_kinematics is not None and getattr(self.coupled_kinematics, "sheet_inner_radius_mm", None) is not None:
                next_outer_r = self.coupled_kinematics.sheet_inner_radius_mm + self.coupled_kinematics.sheet_thickness_mm
            else:
                raise PressCycleError(
                    f"UNSUPPORTED: Missing authoritative BendZone geometry for next bend '{nxt.bend_id}'"
                )
            end_pose = WorkpiecePlacement3D.compute_placement(
                self.sequence.fold_state,
                nxt.bend_id,
                support_plane_zm=next_outer_r,
                align_bisector=True,
                align_bend_arc_center=True,
                reverse_axis=bool(
                    getattr(self, "loading_orientation_by_bend", {}).get(
                        nxt.bend_id, False
                    )
                ),
                rear_panel_vertices_xy=getattr(self, "rear_panel_vertices_xy", None),
            )
            from smbs_core.kinematics2d.workpiece_transfer_3d import build_transfer_3d
            self.transfer_3d = build_transfer_3d(
                start_pose,
                end_pose,
                max_linear_mm_s=GEOMETRIC_PREVIEW_TRANSFER_MAX_LINEAR_MM_S,
                max_angular_deg_s=GEOMETRIC_PREVIEW_TRANSFER_MAX_ANGULAR_DEG_S,
            )
            self.transfer_duration_s = max(0.05, float(self.transfer_3d.duration_s))
            self.phase = CyclePhase.TRANSFER
        except Exception as exc:
            # Failsafe halt on transfer trajectory construction failure:
            # NEVER continue or fake a transfer duration. Retain exact FoldState angles
            # and freeze controller playback with explicit fault reason.
            self.transfer_3d = None
            self.transfer_fault = (
                f"TRANSFER_TRAJECTORY_FAILED: {active.bend_id}->{nxt.bend_id}: {exc}"
            )
            self.pause()
            raise PressCycleError(self.transfer_fault) from exc

    def advance_time(self, dt_sec: float) -> bool:
        """Advance the press brake working cycle by `dt_sec` seconds.

        In `BEND_ONLY` mode: delegates to `BendSequenceController.advance_time(dt_sec)`
        and keeps tooling aligned to the active bend.
        In `FULL_CYCLE` mode: advances at most one phase transition per tick so every
        stage (`APPROACH`, `CONTACT`, `BENDING`, `BOTTOM`, `RETRACT`, `STEP_COMPLETE`)
        is observable and deterministic.
        """
        if self.sequence.status != PlaybackStatus.PLAYING:
            return False
        if not math.isfinite(dt_sec) or dt_sec <= 0.0:
            return False

        if self.execution_mode == CycleExecutionMode.BEND_ONLY:
            prev_idx = self.sequence.active_index
            changed = self.sequence.advance_time(dt_sec)
            if self.sequence.active_index != prev_idx:
                self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
            if self.sequence.status == PlaybackStatus.COMPLETED:
                self.phase = CyclePhase.COMPLETED
            else:
                self.phase = CyclePhase.BENDING
            return changed

        effective_dt = min(float(dt_sec), 0.1)

        if self.phase == CyclePhase.IDLE or self.phase == CyclePhase.COMPLETED:
            self.phase = (
                CyclePhase.LOAD_TO_GAUGE
                if self.initial_feed_preview_mm > 0.0
                and self.sequence.active_index == 0
                else CyclePhase.APPROACH
            )
            self._set_punch_z(self.upper_z_mm)
            return True

        # First-sheet visual introduction from operator side (-s) to the
        # selected rear gauge. Does not advance the bend or ram.
        if self.phase == CyclePhase.LOAD_TO_GAUGE:
            if (not math.isfinite(self.initial_feed_preview_mm)
                    or not math.isfinite(self.initial_feed_preview_speed_mm_s)
                    or self.initial_feed_preview_mm <= 0.0
                    or self.initial_feed_preview_speed_mm_s <= 0.0):
                raise PressCycleError("invalid_initial_gauge_feed_preview_settings")
            delta = (self.initial_feed_preview_speed_mm_s * effective_dt
                     / self.initial_feed_preview_mm)
            self.initial_feed_progress = min(1.0, self.initial_feed_progress + delta)
            if self.initial_feed_progress >= 1.0:
                self.phase = CyclePhase.APPROACH
            return True

        # ------------------------------------------------------------------
        # 6.1 APPROACH: Punch moves down (-Z), sheet angle remains invariant
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.APPROACH:
            next_z = self.ram_z_mm - self.config.approach_speed_mm_s * effective_dt
            if next_z <= self.contact_z_mm + 1e-6:
                self._set_punch_z(self.contact_z_mm)
                self.phase = CyclePhase.CONTACT
                return True
            self._set_punch_z(next_z)
            return True

        # ------------------------------------------------------------------
        # 6.2 CONTACT: Contact boundary reached (GEOMETRIC_CONTACT or ASSUMED_CONTACT)
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.CONTACT:
            self._set_punch_z(self.contact_z_mm)
            self.phase = CyclePhase.BENDING
            return True

        # ------------------------------------------------------------------
        # 6.3 BENDING: Punch moves from contact_z_mm to bottom_z_mm while
        #              active bend angle interpolates from 0 deg to target_angle_deg
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.BENDING:
            bid = self.active_bend_id
            target = self.target_angle_deg
            sign = 1.0 if target >= 0.0 else -1.0
            target_mag = abs(target)
            cur_mag = abs(self.current_angle_deg)

            duration_s = self.config.effective_bend_duration_s(target)
            rate_deg_s = target_mag / duration_s if duration_s > 1e-9 else self.config.bend_rate_deg_s
            next_mag = cur_mag + rate_deg_s * effective_dt

            if target_mag <= ANGLE_EPS or next_mag >= target_mag - ANGLE_EPS:
                # Exact target angle reached -> transition to BOTTOM
                self.sequence.fold_state.set_angle(bid, target)
                stroke = (
                    self.coupled_kinematics.stroke_for_angle(target_mag)
                    if self.coupled_kinematics is not None
                    else self.config.preview_stroke_mm
                )
                self._set_punch_z(self.contact_z_mm - stroke)
                self.bottom_z_mm = self.ram_z_mm
                self.dwell_remaining_s = float(self.config.bottom_dwell_s)
                self.phase = CyclePhase.BOTTOM
                return True

            # Advance angle and punch stroke coupled
            self.sequence.fold_state.set_angle(bid, sign * next_mag)
            stroke = (
                self.coupled_kinematics.stroke_for_angle(next_mag)
                if self.coupled_kinematics is not None
                else (next_mag / max(1e-6, target_mag)) * self.config.preview_stroke_mm
            )
            self._set_punch_z(self.contact_z_mm - stroke)
            return True

        # ------------------------------------------------------------------
        # 6.4 BOTTOM: Target angle held exact, punch at bottom_z_mm, dwell timer
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.BOTTOM:
            self._set_punch_z(self.bottom_z_mm)
            if self.dwell_remaining_s > 1e-6:
                self.dwell_remaining_s = max(0.0, self.dwell_remaining_s - effective_dt)
                if self.dwell_remaining_s > 1e-6:
                    return True
            self.dwell_remaining_s = 0.0
            self.phase = CyclePhase.RETRACT
            return True

        # ------------------------------------------------------------------
        # 6.5 RETRACT: Punch moves back up (+Z) to upper_z_mm, sheet angle held
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.RETRACT:
            next_z = self.ram_z_mm + self.config.retract_speed_mm_s * effective_dt
            if next_z >= self.upper_z_mm - 1e-6:
                self._set_punch_z(self.upper_z_mm)
                self.phase = CyclePhase.STEP_COMPLETE
                return True
            self._set_punch_z(next_z)
            return True

        # ------------------------------------------------------------------
        # 6.6 STEP_COMPLETE: Advance to next operation in bend_order ONLY
        #                    after RETRACT has completed, or finish at COMPLETED
        # ------------------------------------------------------------------
        if self.phase == CyclePhase.STEP_COMPLETE:
            if self.sequence.active_index + 1 < self.sequence.total_steps:
                if self.animate_workpiece_transfer:
                    self._begin_workpiece_transfer()
                    return True
                self.sequence.active_index += 1
                self._sync_bounds_and_orientation_for_active_bend(reposition_tools=True)
                self.phase = CyclePhase.APPROACH
                return True
            self.phase = CyclePhase.COMPLETED
            self.sequence.status = PlaybackStatus.COMPLETED
            return True

        if self.phase == CyclePhase.TRANSFER:
            if self.transfer_fault is not None:
                self.pause()
                return False
            self.transfer_progress = min(
                1.0, self.transfer_progress + effective_dt/self.transfer_duration_s
            )
            if self.transfer_progress >= 1.0:
                self.sequence.active_index += 1
                self.transfer_progress = 0.0
                self.transfer_3d = None
                # Machine tooling remains stationary. The sheet is transported
                # to the next bend; repositioning the die itself would reverse
                # the intended feed animation.
                self._sync_bounds_and_orientation_for_active_bend(reposition_tools=False)
                sec = self._current_cross_section()
                self._rebase_contact_to_die_supported_sheet(sec)
                self._park_punch_at_upper_position()
                if self.tooling.aligned:
                    self.tooling.aligned_bend_id = self.active_bend_id
                self.phase = CyclePhase.APPROACH
            return True

        return False

    def current_workpiece_placement_3d(self, desired_midpoint_xm: float = 0.0) -> PlacementTransform3D:
        """Authoritative rigid 3D workpiece placement in Machine Coordinates.

        Returns:
            - If phase == TRANSFER and transfer_3d is active:
              interpolated rigid 3D pose at elapsed transfer time: Transfer3D.pose_at(t).
            - Otherwise:
              rigid 3D placement for active_bend_id: WorkpiecePlacement3D.compute_placement(...).
        All panels receive this exact spatial isometry; det(R) == +1.0.
        """
        if self.transfer_fault is not None:
            raise PressCycleError(self.transfer_fault)
        fold_state = self.sequence.fold_state
        if self.phase == CyclePhase.TRANSFER:
            elapsed_s = self.transfer_progress * self.transfer_duration_s
            if self.transfer_3d is not None:
                return self.transfer_3d.pose_at(elapsed_s)

        # Coupled penetration during BENDING, BOTTOM, RETRACT and explicit
        # manual scrub. Scrubbing does NOT change the automatic cycle phase.
        align_bisector = False
        align_arc_center = False
        if self.coupled_kinematics is not None and (
            self.execution_mode == CycleExecutionMode.FULL_CYCLE
            or self._manual_preview_active
        ):
            bz = None
            if self.active_bend_id in fold_state.bends:
                bz = fold_state.bends[self.active_bend_id].bend_zone
            if bz is not None:
                outer_r = bz.outer_radius_mm
            elif getattr(self.coupled_kinematics, "sheet_inner_radius_mm", None) is not None and getattr(self.coupled_kinematics, "sheet_thickness_mm", None) is not None:
                outer_r = self.coupled_kinematics.sheet_inner_radius_mm + self.coupled_kinematics.sheet_thickness_mm
            else:
                raise PressCycleError(
                    f"UNSUPPORTED: Missing authoritative BendZone geometry for active bend '{self.active_bend_id}'"
                )

            if self.phase == CyclePhase.BENDING or self._manual_preview_active:
                support_z = self.preview_workpiece_support_zm()
                align_bisector = True
                align_arc_center = True
            elif self.phase in (CyclePhase.BOTTOM, CyclePhase.RETRACT, CyclePhase.STEP_COMPLETE):
                target_angle = abs(self.target_angle_deg)
                stroke = self.coupled_kinematics.stroke_for_angle(target_angle)
                support_z = outer_r - stroke
                align_bisector = True
                align_arc_center = True
            else:
                # IDLE, APPROACH, CONTACT: sheet outer surface rests flat on die shoulders (Z_m = 0)
                support_z = outer_r
                align_bisector = True
                align_arc_center = True
        else:
            support_z = 0.0

        pose = WorkpiecePlacement3D.compute_placement(
            fold_state,
            self.active_bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            support_plane_zm=support_z,
            align_bisector=align_bisector,
            align_bend_arc_center=align_arc_center,
            reverse_axis=bool(
                getattr(self, "loading_orientation_by_bend", {}).get(
                    self.active_bend_id, False
                )
            ),
            rear_panel_vertices_xy=getattr(self, "rear_panel_vertices_xy", None),
        )
        if self.phase == CyclePhase.LOAD_TO_GAUGE:
            # Machine s = -Y_m. Positive machine Y translates the entire
            # sheet left towards the operator, then feeds it to the right.
            # Stationary tooling and backgauge never move with the sheet.
            from .workpiece_placement_3d import PlacementTransform3D
            remaining = (1.0 - self.initial_feed_progress) * self.initial_feed_preview_mm
            return PlacementTransform3D(
                rotation=pose.rotation,
                translation=(
                    pose.translation[0], pose.translation[1] + remaining,
                    pose.translation[2],
                ),
            )
        return pose

    def coupled_telemetry(self) -> Dict[str, Any]:
        """Return coupled kinematics status and coordinates for JSONL tracing."""
        cur_ang = self.current_angle_deg
        if self.coupled_kinematics is not None:
            ck = self.coupled_kinematics
            h = self.ram_stroke_mm
            contacts = ck.die_shoulder_contacts(cur_ang)
            zone_pos = ck.bend_zone_position(cur_ang)
            clearance = ck.punch_clearance(cur_ang, self.ram_z_mm)
            is_valid, val_status, _ = ck.validate_geometric_constraints(cur_ang, self.ram_z_mm)
            return {
                "punch_stroke_mm": round(h, 4),
                "bend_angle_deg": round(cur_ang, 4),
                "contact_points": {
                    "punch": [0.0, round(self.ram_z_mm, 4)],
                    "die_left": [round(contacts[0][0], 4), round(contacts[0][1], 4)],
                    "die_right": [round(contacts[1][0], 4), round(contacts[1][1], 4)],
                },
                "bend_zone_position": {
                    "inner_apex": [round(zone_pos["inner_apex"][0], 4), round(zone_pos["inner_apex"][1], 4)],
                    "outer_apex": [round(zone_pos["outer_apex"][0], 4), round(zone_pos["outer_apex"][1], 4)],
                },
                "punch_clearance_mm": round(clearance, 4),
                "validation_status": val_status,
                "tooling_status": ck.tooling_status,
                "collision_validated": False,
                "support_validated": False,
            }
        return {
            "punch_stroke_mm": round(self.ram_stroke_mm, 4),
            "bend_angle_deg": round(cur_ang, 4),
            "contact_points": None,
            "bend_zone_position": None,
            "punch_clearance_mm": None,
            "validation_status": "UNVERIFIED: no coupled kinematics",
            "tooling_status": "NOT_SPECIFIED",
            "collision_validated": False,
            "support_validated": False,
        }

    def status_line(self) -> str:
        """Return explicit, non-misleading status line for GUI display."""
        if self.execution_mode == CycleExecutionMode.BEND_ONLY:
            return "{} | Mode: BEND_ONLY ({})".format(
                self.sequence.status_line(),
                self.simulation_mode.value,
            )
        return (
            "Step {}/{} | {} | {} | Ram Z: {:+.2f} mm (Stroke: {:.2f} mm) | "
            "Bend: {:+.1f}° / {:+.1f}° | {} | {} | {}".format(
                self.active_index + 1,
                self.total_steps,
                self.active_bend_id,
                self.phase.value,
                self.ram_z_mm,
                self.ram_stroke_mm,
                self.current_angle_deg,
                self.target_angle_deg,
                self.simulation_mode.value,
                self.contact_kind.value,
                self.orientation_status.value,
            )
            + " | " + self.contact_tracking_status
            + (
                " | HANDLING {:.0f}% | next-bend nominal feed {:+.1f} mm | "
                "visualized via continuous workpiece pose".format(
                    100.0 * self.transfer_progress,
                    self.transfer_target_feed_mm,
                )
                if self.phase == CyclePhase.TRANSFER else ""
            )
        )
