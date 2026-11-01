"""
validator.py - Single Authoritative Physical Oracle for Step Validation (Headless Core)
"""

import logging
from typing import Dict, Any, Optional, Tuple
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.bend_transform import BendTransform
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.geometry_backend.base import GeometryBackend
from smbs_core.models.tooling import AlignmentStatus, GeometryStatus

logger = logging.getLogger(__name__)


class ValidationResult:
    def __init__(
        self,
        valid: bool,
        reason: str,
        stage: str,
        next_state: Optional[FoldState] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
        collision_status: CollisionStatus = CollisionStatus.UNKNOWN,
        tooling_status: GeometryStatus = GeometryStatus.MISSING,
        alignment_status: AlignmentStatus = AlignmentStatus.CANNOT_VERIFY,
        has_unknown: bool = False
    ):
        self.valid = valid
        self.reason = reason
        self.stage = stage
        self.next_state = next_state
        self.diagnostics = diagnostics or {}
        self.collision_status = collision_status
        self.tooling_status = tooling_status
        self.alignment_status = alignment_status
        self.has_unknown = has_unknown

    def __repr__(self) -> str:
        status = "VALID" if self.valid else f"INVALID[{self.stage}:{self.reason}]"
        return f"<ValidationResult {status} (coll:{self.collision_status.value}, tool:{self.tooling_status.value}, align:{self.alignment_status.value})>"


class PhysicalValidator:
    def __init__(self, machine: Optional[PressBrakeMachine], backend: GeometryBackend):
        self.machine = machine or PressBrakeMachine()
        self.backend = backend
        self.assembly = self.machine.setup_assembly(self.backend) if hasattr(self.machine, 'setup_assembly') else None
        self.detector = CollisionDetector(self.assembly or self.machine, self.backend)
        self.invalid_transitions: Dict[Tuple[str, str], ValidationResult] = {}

    @property
    def tooling_status(self) -> GeometryStatus:
        if not self.assembly or not self.assembly.punch or not self.assembly.die:
            return GeometryStatus.MISSING
        p_status = self.assembly.punch.geometry_status
        d_status = self.assembly.die.geometry_status
        if p_status == GeometryStatus.REAL and d_status == GeometryStatus.REAL:
            return GeometryStatus.REAL
        if p_status == GeometryStatus.MISSING or d_status == GeometryStatus.MISSING:
            return GeometryStatus.MISSING
        return GeometryStatus.SIMPLIFIED

    @property
    def alignment_status(self) -> AlignmentStatus:
        if not self.assembly or not self.assembly.punch or not self.assembly.die:
            return AlignmentStatus.CANNOT_VERIFY
        res = self.assembly.place(self.assembly.punch, self.assembly.die)
        return res.status

    def validate_step(
        self,
        current_state: FoldState,
        bend_record: BendRecord,
        panel_graph: Optional[PanelGraph] = None,
        tooling_override: Optional[Dict[str, Any]] = None,
        unknown_policy: str = "PRODUCTION"
    ) -> ValidationResult:
        """Validate every viable mounting candidate through the entire stroke.

        A candidate is selected on APPROACH and locked for TOOL/MACHINE/
        TRAJECTORY/RETRACT. A rejected candidate cannot veto another setup.
        The topology transform is independent of mounting and is computed once.
        """
        bend_id = bend_record.id
        current_fp = current_state.fingerprint()
        cache_key = (current_fp, bend_id, unknown_policy)
        if cache_key in self.invalid_transitions:
            return self.invalid_transitions[cache_key]

        self.detector.begin_step()
        try:
            if bend_id not in current_state.remaining_bends:
                return ValidationResult(
                    valid=False, reason=f"Bend {bend_id} not remaining",
                    stage="consistency", collision_status=CollisionStatus.INVALID,
                    tooling_status=self.tooling_status, alignment_status=self.alignment_status)

            axis_origin, axis_dir = current_state.bend_axes.get(
                bend_id, (bend_record.axis_origin, bend_record.axis_direction))
            bend_info = bend_record.to_dict()
            bend_info.update({"origin": axis_origin, "axis": axis_dir, "id": bend_id})
            moving_panels = panel_graph.moving_subtree(bend_id) if panel_graph else set()
            if not moving_panels:
                return ValidationResult(
                    valid=False, reason=f"No moving panels for {bend_id}",
                    stage="UNFOLD_TRANSFORM", collision_status=CollisionStatus.INVALID,
                    tooling_status=self.tooling_status, alignment_status=self.alignment_status)
            try:
                unfolded_shape = BendTransform.rotate_subtree(
                    self.backend, current_state.shape, axis_origin, axis_dir,
                    -bend_record.signed_angle, moving_panels, panel_graph)
            except Exception as error:
                return ValidationResult(
                    valid=False, reason=f"Unfold transformation failed: {error}",
                    stage="UNFOLD_TRANSFORM", collision_status=CollisionStatus.INVALID,
                    tooling_status=self.tooling_status, alignment_status=self.alignment_status)
            if unfolded_shape is None or unfolded_shape == current_state.shape:
                return ValidationResult(
                    valid=False, reason="Unfold transformation returned unchanged geometry",
                    stage="UNFOLD_TRANSFORM", collision_status=CollisionStatus.INVALID,
                    tooling_status=self.tooling_status, alignment_status=self.alignment_status)

            # This check does not depend on placement: run once.
            self_res = self.detector.self_collision(unfolded_shape, bend_id, panel_graph)
            if self_res.status == CollisionStatus.INVALID or (
                self_res.status == CollisionStatus.UNKNOWN and unknown_policy == "PRODUCTION"
            ):
                return ValidationResult(
                    valid=False, reason=self_res.message, stage="SELF",
                    collision_status=self_res.status,
                    tooling_status=self.tooling_status, alignment_status=self.alignment_status,
                    has_unknown=self_res.status == CollisionStatus.UNKNOWN)

            failures = []
            candidate_count = 0
            # The enumeration is bounded by two signs × four valid normals.
            # Do not retry a failed setup for the same physical stroke.
            seen_placement_keys = set()
            while candidate_count < 8:
                # APPROACH picks a fresh, not-yet-rejected orientation and locks it.
                approach = self.detector.approach_collision(current_state.shape, bend_info)
                placement_key = getattr(self.detector, "active_placement_key", None)
                # Mock backends have no OCC placement; allow one virtual setup.
                is_mock = getattr(self.backend, "name", "") == "mock" or any(
                    "mock" in cls.__name__.lower() for cls in type(self.backend).__mro__)
                if placement_key is None and is_mock and candidate_count == 0:
                    placement_key = ("mock",)
                if placement_key is None:
                    # Placement cannot be established. This is not proof of an impossible bend:
                    # the tool assembly/datum or geometry might be incomplete.
                    failures.append(("PLACEMENT", approach))
                    break

                if placement_key in seen_placement_keys:
                    from smbs_core.Physics.collision_detector import CollisionResult
                    failures.append(("PLACEMENT", CollisionResult(
                        CollisionStatus.UNKNOWN, "Placement selector returned the same candidate twice")))
                    break
                seen_placement_keys.add(placement_key)
                candidate_count += 1
                candidate_unknown = (self_res.status == CollisionStatus.UNKNOWN)
                failure = None
                # Candidate backgauge assessment
                def _check_candidate_backgauge():
                    from smbs_core.Physics.backgauge_calculator import BackgaugeCalculator
                    from smbs_core.Physics.collision_detector import CollisionResult
                    bg_calc = BackgaugeCalculator(self.machine)
                    binfo = {
                        "length": bend_record.length,
                        "bend_id": bend_id,
                        "origin": bend_record.axis_origin,
                        "axis": bend_record.axis_direction,
                        "signed_angle": bend_record.signed_angle,
                    }
                    cand_shape = getattr(self.detector, "place_part_on_tool_station", lambda s, b: None)(current_state.shape, binfo)
                    part_th = bend_record.thickness if bend_record.thickness is not None else 2.0
                    bg_res = bg_calc.compute_step_backgauge(
                        step_number=1,
                        bend_info=binfo,
                        part_thickness=part_th,
                        confirmed_geometry=True,
                        placement_key=placement_key,
                        placed_shape=cand_shape
                    )
                    # If coordinates exceed machine axis limits, fail candidate closed
                    if bg_res.get("status") == "INVALID":
                        return CollisionResult(CollisionStatus.INVALID, f"Backgauge limit exceeded: {bg_res.get('warning')}")

                    # Check collision with physical backgauge fingers if present
                    if hasattr(self.assembly, 'backgauge') and self.assembly.backgauge and getattr(self.assembly.backgauge, 'shape', None):
                        coll_res = self.detector.backgauge_collision(cand_shape, self.assembly.backgauge.shape)
                        if not coll_res.is_valid:
                            return coll_res

                    # Feasible / reachable
                    return CollisionResult(CollisionStatus.VALID, "Backgauge candidate feasible")

                for stage, check in (
                    ("APPROACH", lambda: approach),
                    ("TOOL", lambda: self.detector.tool_collision(unfolded_shape, bend_info, bend_id, panel_graph)),
                    ("MACHINE", lambda: self.detector.machine_collision(unfolded_shape, bend_info, bend_id, panel_graph)),
                    ("BEND_TRAJECTORY", lambda: self.detector.trajectory_collision(
                        current_state.shape, bend_info, bend_id=bend_id,
                        panel_graph=panel_graph, dtheta=5.0)),
                    ("RETRACT", lambda: self.detector.retract_collision(unfolded_shape, bend_info)),
                    ("BACKGAUGE", _check_candidate_backgauge),
                ):
                    result = check()
                    if result.status == CollisionStatus.UNKNOWN:
                        candidate_unknown = True
                    if result.status == CollisionStatus.INVALID or (
                        result.status == CollisionStatus.UNKNOWN and unknown_policy == "PRODUCTION"
                    ):
                        failure = (stage, result)
                        break

                if failure is None:
                    next_state = current_state.apply_unfold(
                        bend_id=bend_id, unfolded_shape=unfolded_shape,
                        moving_panels=moving_panels)
                    return ValidationResult(
                        valid=True,
                        reason="Step validated physically." if not candidate_unknown else
                               "Step accepted in EXPLORATORY mode with UNKNOWN subchecks.",
                        stage="valid", next_state=next_state,
                        diagnostics={"bend_id": bend_id,
                                     "signed_angle": bend_record.signed_angle,
                                     "mounting_candidates_tried": candidate_count,
                                     "mounting_key": placement_key},
                        collision_status=CollisionStatus.UNKNOWN if candidate_unknown else CollisionStatus.VALID,
                        tooling_status=self.tooling_status,
                        alignment_status=self.alignment_status,
                        has_unknown=candidate_unknown)

                # Record stage and failure reason on candidate log if available
                if self.detector.candidate_placement_logs:
                    for cl in reversed(self.detector.candidate_placement_logs):
                        if cl.get("placement_key") == placement_key:
                            cl["status"] = "REJECTED"
                            cl["rejection_stage"] = failure[0]
                            cl["rejection_reason"] = failure[1].message
                            break

                failures.append(failure)
                if is_mock:
                    break
                self.detector.reject_active_placement()
            # At most eight geometrically distinct orientation candidates exist in
            # the present two-sign/four-normal convention.

            # Prioritize candidate-level physical invalidation stages (e.g. BACKGAUGE, TOOL, BEND_TRAJECTORY)
            # over the terminal PLACEMENT sentinel when candidates were evaluated.
            invalids = [(stg, res) for stg, res in failures if res.status == CollisionStatus.INVALID]
            unknowns = [(stg, res) for stg, res in failures if res.status == CollisionStatus.UNKNOWN]
            if invalids:
                stage, result = invalids[0]
                status = CollisionStatus.INVALID
            elif unknowns:
                stage, result = unknowns[0]
                status = CollisionStatus.UNKNOWN
            elif failures:
                stage, result = failures[0]
                status = CollisionStatus.INVALID
            else:
                from smbs_core.Physics.collision_detector import CollisionResult
                stage = "PLACEMENT"
                result = CollisionResult(CollisionStatus.UNKNOWN, "No placement candidates evaluated")
                failures.append((stage, result))
                status = CollisionStatus.UNKNOWN
            summary = "; ".join(
                f"{stage}: {res.message}" for stage, res in failures[:8])
            out = ValidationResult(
                valid=False,
                reason=f"All {candidate_count} mountings rejected: {summary}",
                stage=stage, collision_status=status,
                diagnostics={"bend_id": bend_id, "mounting_candidates_tried": candidate_count,
                             "failures": [{"stage": s, "status": r.status.value,
                                           "reason": r.message} for s, r in failures]},
                tooling_status=self.tooling_status,
                alignment_status=self.alignment_status,
                has_unknown=bool(unknowns))
            # Cache only final failure, not rejection of one mounting candidate.
            self.invalid_transitions[cache_key] = out
            return out
        finally:
            self.detector.end_step()
