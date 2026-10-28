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
        bend_id = bend_record.id
        current_fp = current_state.fingerprint()

        cache_key = (current_fp, bend_id)
        if cache_key in self.invalid_transitions:
            return self.invalid_transitions[cache_key]

        if hasattr(self.detector, 'begin_step'):
            self.detector.begin_step()

        try:
            if bend_id not in current_state.remaining_bends:
                return ValidationResult(
                    valid=False,
                    reason=f"Bend {bend_id} is not in remaining_bends set.",
                    stage="consistency",
                    collision_status=CollisionStatus.INVALID,
                    tooling_status=self.tooling_status,
                    alignment_status=self.alignment_status
                )

            current_axis_origin, current_axis_dir = current_state.bend_axes.get(
                bend_id,
                (bend_record.axis_origin, bend_record.axis_direction)
            )

            bend_info = bend_record.to_dict()
            bend_info["origin"] = current_axis_origin
            bend_info["axis"] = current_axis_dir

            step_has_unknown = False

            # Helper for collision checks with unknown_policy handling
            def evaluate_collision(coll_res: Any, stage_name: str):
                nonlocal step_has_unknown
                if coll_res.status == CollisionStatus.INVALID:
                    logger.info(f"Reject [{bend_id}] stage={stage_name}: {coll_res.message}")
                    res = ValidationResult(
                        valid=False, reason=coll_res.message, stage=stage_name,
                        collision_status=coll_res.status, tooling_status=self.tooling_status, alignment_status=self.alignment_status,
                        has_unknown=False
                    )
                    self.invalid_transitions[cache_key] = res
                    return res
                if coll_res.status == CollisionStatus.UNKNOWN:
                    if unknown_policy == "PRODUCTION":
                        logger.info(f"Reject [{bend_id}] stage={stage_name} [PRODUCTION]: {coll_res.message}")
                        res = ValidationResult(
                            valid=False, reason=f"[PRODUCTION] {coll_res.message}", stage=stage_name,
                            collision_status=coll_res.status, tooling_status=self.tooling_status, alignment_status=self.alignment_status,
                            has_unknown=True
                        )
                        self.invalid_transitions[cache_key] = res
                        return res
                    else:  # EXPLORATORY
                        step_has_unknown = True
                return None

            # 1. APPROACH
            app_res = self.detector.approach_collision(current_state.shape, bend_info)
            early_exit = evaluate_collision(app_res, "APPROACH")
            if early_exit is not None:
                return early_exit

            # 2. UNFOLD_TRANSFORM
            moving_panels = panel_graph.moving_subtree(bend_id) if panel_graph else set()
            if not moving_panels:
                logger.info(f"Reject [{bend_id}] stage=UNFOLD_TRANSFORM: empty moving panels subtree.")
                res = ValidationResult(
                    valid=False, reason=f"Cannot unfold bend {bend_id}: empty moving panels subtree.", stage="UNFOLD_TRANSFORM",
                    collision_status=CollisionStatus.INVALID, tooling_status=self.tooling_status, alignment_status=self.alignment_status
                )
                self.invalid_transitions[cache_key] = res
                return res

            try:
                unfolded_shape = BendTransform.rotate_subtree(
                    self.backend,
                    current_state.shape,
                    current_axis_origin,
                    current_axis_dir,
                    -bend_record.signed_angle,
                    moving_panels,
                    panel_graph
                )
            except Exception as trsf_err:
                logger.info(f"Reject [{bend_id}] stage=UNFOLD_TRANSFORM: {trsf_err}")
                res = ValidationResult(
                    valid=False, reason=f"Unfold transformation failed: {trsf_err}", stage="UNFOLD_TRANSFORM",
                    collision_status=CollisionStatus.INVALID, tooling_status=self.tooling_status, alignment_status=self.alignment_status
                )
                self.invalid_transitions[cache_key] = res
                return res

            if unfolded_shape is None or unfolded_shape == current_state.shape:
                logger.info(f"Reject [{bend_id}] stage=UNFOLD_TRANSFORM: geometry was unchanged after unfolding.")
                res = ValidationResult(
                    valid=False, reason=f"Unfold transformation resulted in unchanged geometry for bend {bend_id}", stage="UNFOLD_TRANSFORM",
                    collision_status=CollisionStatus.INVALID, tooling_status=self.tooling_status, alignment_status=self.alignment_status
                )
                self.invalid_transitions[cache_key] = res
                return res

            # 3. SELF
            self_res = self.detector.self_collision(unfolded_shape, bend_id, panel_graph)
            early_exit = evaluate_collision(self_res, "SELF")
            if early_exit is not None:
                return early_exit

            # 4. TOOL
            tool_res = self.detector.tool_collision(unfolded_shape, bend_info, bend_id, panel_graph)
            early_exit = evaluate_collision(tool_res, "TOOL")
            if early_exit is not None:
                return early_exit

            # 5. MACHINE
            mach_res = self.detector.machine_collision(unfolded_shape, bend_info, bend_id, panel_graph)
            early_exit = evaluate_collision(mach_res, "MACHINE")
            if early_exit is not None:
                return early_exit

            # 6. BEND_TRAJECTORY
            traj_res = self.detector.trajectory_collision(current_state.shape, bend_info, bend_id=bend_id, panel_graph=panel_graph, dtheta=5.0)
            early_exit = evaluate_collision(traj_res, "BEND_TRAJECTORY")
            if early_exit is not None:
                return early_exit

            # 7. RETRACT
            ret_res = self.detector.retract_collision(unfolded_shape, bend_info)
            early_exit = evaluate_collision(ret_res, "RETRACT")
            if early_exit is not None:
                return early_exit

            next_fold_state = current_state.apply_unfold(
                bend_id=bend_id,
                unfolded_shape=unfolded_shape,
                moving_panels=moving_panels
            )

            step_coll_status = CollisionStatus.UNKNOWN if step_has_unknown else CollisionStatus.VALID

            return ValidationResult(
                valid=True,
                reason="Step validated physically." if not step_has_unknown else "Step accepted under EXPLORATORY policy with UNKNOWN subchecks.",
                stage="valid",
                next_state=next_fold_state,
                diagnostics={"bend_id": bend_id, "signed_angle": bend_record.signed_angle},
                collision_status=step_coll_status,
                tooling_status=self.tooling_status,
                alignment_status=self.alignment_status,
                has_unknown=step_has_unknown
            )
        finally:
            if hasattr(self.detector, 'end_step'):
                self.detector.end_step()
