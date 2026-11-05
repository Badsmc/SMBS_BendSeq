"""D6.2: candidate machine-step 2.5D evidence adapter.

Connects the existing D6 DFS search to the authoritative FoldState,
WorkpiecePlacement3D, CoupledAirBendingKinematics, machine cross-section
extraction, and D5 punch/die polygon oracle.

IMPORTANT: a 2D cross section is *not* a complete 3D machine check. This
oracle never certifies the whole list of production verification scopes.
Even a CLEAR result is partial evidence and is safe only for diagnostic
candidate search (plan_forward(..., allow_partial_preview=True)).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from .coupled_kinematics_2d import CoupledAirBendingKinematics
from .d5_contact_validation import D51Status, check_d51_machine_section
from .machine_section_3d import compute_machine_cutting_plane_section
from .panel_kinematics import FoldState
from .sequence_search import Feasibility, StepEvidence
from .tooling_25d import ToolingSetup2D, ValidationStatus
from .workpiece_placement_3d import WorkpiecePlacement3D


@dataclass(frozen=True)
class CandidateCheck:
    bend_id: str
    previous_bend_id: str | None
    status: Feasibility
    reason: str
    sampled_phases: tuple[str, ...]
    checked_interactions: int
    source: str = "2D_SECTION_SAMPLED_PREVIEW_NOT_CERTIFIED"


class PreviewStepOracle25D:
    """Read-only candidate validator against real selected punch/die profiles.

    No FreeCAD API or GUI state is mutated. A fresh FoldState and independent
    tooling positioning instance are created for every candidate; they share
    read-only ToolProfile geometric data with the selected tooling library.

    A 2D cross section cannot validate backgauge, full longitudinal span,
    clamps, station frame, 3D self-overlap, or the transfer swept volume.
    Consequently this oracle NEVER fills all REQUIRED_SCOPES.
    """

    def __init__(
        self,
        panel_vertices_xy: Mapping[str, Any],
        tooling_setup: ToolingSetup2D,
        *,
        tolerance_mm: float = 0.02,
        fractions: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0),
        clearance_mm: float = 5.0,
    ):
        if not isinstance(tooling_setup, ToolingSetup2D):
            raise TypeError("Expected an actual ToolingSetup2D")
        if not isfinite(tolerance_mm) or not 1e-5 <= tolerance_mm <= 1.0:
            raise ValueError("Invalid cross-section tolerance")
        if not isfinite(clearance_mm) or clearance_mm <= 0:
            raise ValueError("Invalid ram clearance")
        if not fractions or any(not isfinite(f) or f < 0 or f > 1 for f in fractions):
            raise ValueError("Sampling fractions must belong to [0,1]")
        if 0.0 not in fractions or 1.0 not in fractions:
            raise ValueError("Sampling must include both stroke endpoints")
        self.panel_vertices_xy = dict(panel_vertices_xy)
        self.tooling_setup = tooling_setup
        self.tolerance_mm = float(tolerance_mm)
        self.fractions = tuple(sorted(set(fractions)))
        self.clearance_mm = float(clearance_mm)
        self.history: list[CandidateCheck] = []

    def _finish(self, bend_id, previous, status, reason, phases, checks):
        record = CandidateCheck(
            str(bend_id), previous, status, str(reason),
            tuple(phases), int(checks),
        )
        self.history.append(record)
        return StepEvidence(
            status, str(reason),
            frozenset(("CROSS_SECTION_SAMPLES",)) if status is Feasibility.CLEAR
            else frozenset(),
        )

    def __call__(
        self, state: FoldState, bend_id: str, previous_bend_id: str | None,
    ) -> StepEvidence:
        if not isinstance(state, FoldState):
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED, "invalid_fold_state", (), 0)
        if bend_id not in state.bends or (previous_bend_id is not None
                                        and previous_bend_id not in state.bends):
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED, "unknown_bend_id", (), 0)
        if not self.panel_vertices_xy or any(
            p not in self.panel_vertices_xy or len(self.panel_vertices_xy[p]) < 3
            for p in state.graph.panels
        ):
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED, "incomplete_panel_vertices", (), 0)

        punch = getattr(self.tooling_setup, "punch", None)
        die = getattr(self.tooling_setup, "die", None)
        if punch is None or die is None:
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED, "missing_punch_or_die", (), 0)
        if (getattr(punch, "validation_status", None) != ValidationStatus.GEOMETRY_VERIFIED
                or getattr(die, "validation_status", None) != ValidationStatus.GEOMETRY_VERIFIED):
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED, "tool_profiles_not_verified", (), 0)
        # The coupled model contains reference defaults; never silently accept
        # those estimates as physically verified tooling parameters.
        pd = getattr(punch, "punch_datums", None)
        dd = getattr(die, "die_datums", None)
        required = (
            (pd, "tip_radius_mm"), (pd, "included_angle_deg"),
            (dd, "v_opening_mm"), (dd, "shoulder_radius_mm"),
            (dd, "die_angle_deg"), (dd, "v_depth_mm"),
        )
        if any(
            obj is None or not isinstance(getattr(obj, name, None), (int, float))
            or not isfinite(getattr(obj, name))
            or getattr(obj, name) <= 0
            for obj, name in required
        ):
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED,
                                "tooling_parameters_missing_no_defaults", (), 0)

        phases: list[str] = []
        checks = 0
        try:
            # A private candidate state avoids rewriting the user's active
            # camera, operations table, FoldState or running press controller.
            trial = FoldState(state.graph, state.fixed_panel)
            for b in state.ordered_bends:
                trial.set_angle(b.bend_id, state.angles[b.bend_id])
            selected = trial.bends[bend_id]
            if abs(trial.angles[bend_id]) > 1e-8:
                raise ValueError("candidate_already_bent")

            tooling = ToolingSetup2D(punch=punch, die=die)
            first_pose = WorkpiecePlacement3D.compute_placement(
                trial, bend_id, support_plane_zm=0.0,
                align_bisector=False,
            )
            first_section = compute_machine_cutting_plane_section(
                trial, self.panel_vertices_xy, first_pose,
                active_bend_id=bend_id, segments=32,
            )
            tooling.align_to_cross_section(
                first_section, clearance_mm=self.clearance_mm,
                bend_id=bend_id,
            )
            coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
                tooling, trial, bend_id,
            )
            if coupled.tooling_status != "VERIFIED":
                raise ValueError("coupled_tooling_not_verified")

            def evaluate(phase: str, fraction: float, ram_z: float,
                         bisector: bool) -> str | None:
                nonlocal checks
                angle = fraction * selected.angle_deg
                trial.set_angle(bend_id, angle)
                pose = WorkpiecePlacement3D.compute_placement(
                    trial, bend_id,
                    support_plane_zm=coupled.support_plane_zm(abs(angle)),
                    align_bisector=bisector,
                )
                section = compute_machine_cutting_plane_section(
                    trial, self.panel_vertices_xy, pose,
                    active_bend_id=bend_id, segments=32,
                )
                tooling.set_punch_ram_z(
                    float(ram_z), stroke_mm=max(0.0, coupled.contact_z_mm - ram_z),
                )
                report = check_d51_machine_section(
                    section, tooling, bend_id=bend_id, phase=phase,
                    tolerance_mm=self.tolerance_mm,
                )
                phases.append(phase)
                checks += len(report.findings)
                if report.status is D51Status.COLLISION:
                    first = next(f for f in report.findings if f.status is D51Status.COLLISION)
                    return "COLLISION:" + phase + ":" + first.tool + ":" + first.component + ":" + first.reason
                if report.status is D51Status.UNVERIFIED:
                    first = next(f for f in report.findings if f.status is D51Status.UNVERIFIED)
                    return "UNVERIFIED:" + phase + ":" + first.component + ":" + first.reason
                return None

            # There is no arbitrary success flag. Check actual machine-space
            # sections of the untouched pre-bend state and at signed targets.
            probes = (
                (("APPROACH", 0.0, coupled.contact_z_mm + self.clearance_mm, False),)
                + tuple(
                    ("BENDING", f, coupled.ram_z_for_angle(f * selected.angle_deg),
                     f > 0)
                    for f in self.fractions
                )
                + (("BOTTOM", 1.0, coupled.ram_z_for_angle(selected.angle_deg), True),
                   ("RETRACT", 1.0, coupled.contact_z_mm + self.clearance_mm, True))
            )
            for phase, frac, ram, align in probes:
                problem = evaluate(phase, frac, ram, align)
                if problem is not None:
                    if problem.startswith("COLLISION:"):
                        return self._finish(bend_id, previous_bend_id,
                                            Feasibility.COLLISION,
                                            "2d_section_interference:" + problem,
                                            phases, checks)
                    return self._finish(bend_id, previous_bend_id,
                                        Feasibility.UNVERIFIED, problem,
                                        phases, checks)
        except Exception as exc:
            return self._finish(bend_id, previous_bend_id,
                                Feasibility.UNVERIFIED,
                                "candidate_geometry_unavailable:" + str(exc),
                                phases, checks)

        return self._finish(
            bend_id, previous_bend_id, Feasibility.CLEAR,
            "2d_cross_section_samples_clear_3d_setup_transfer_and_support_unverified",
            phases, checks,
        )
