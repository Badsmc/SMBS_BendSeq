
def _raise_incomplete(bend_id: str, field_name: str):
    raise IncompleteBendError(f"Bend {bend_id} missing mandatory field '{field_name}' in Core/orchestrator.py")
"""
orchestrator.py - Central Coordination Loop for Headless Bend Sequence Planning (SMBS Core)
"""
import time
import os
import logging
from typing import Dict, Any, Optional, List

from .state_cache import StateCache
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.backgauge_calculator import BackgaugeCalculator
from smbs_core.Solvers.astar_backward import AStarBackwardPlanner
from smbs_core.Solvers.greedy_unfold import GreedyUnfoldPlanner
from smbs_core.Solvers.tool_optimizer import ToolOptimizer
from smbs_core.IO.bendseq_writer import BendSeqWriter
from smbs_core.IO.tool_database import ToolDatabase
from smbs_core.geometry_backend.base import GeometryBackend
from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.Solvers.dfs_backward import DFSBackwardPlanner
from smbs_core.Core.bend_graph import BendGraph, BendRecord, IncompleteBendError
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
from OCC.Core.TopoDS import TopoDS_Shape
from smbs_core.Core.fold_state import FoldState
from smbs_core.models.tooling import AlignmentStatus, GeometryStatus

logger = logging.getLogger(__name__)


class BendSeqOrchestrator:
    def __init__(self, backend: GeometryBackend, machine_config: Optional[Dict[str, Any]] = None):
        self.backend = backend
        self.state_cache = StateCache()
        self.machine = PressBrakeMachine.from_config(machine_config) if machine_config else PressBrakeMachine()
        self.extractor = FeatureExtractor(self.backend)
        self.backgauge_calculator = BackgaugeCalculator(self.machine)
        self.tool_database = ToolDatabase()
        self.tool_optimizer = ToolOptimizer(self.tool_database)

    def run_planning(
        self,
        target_shape: Any = None,
        file_path: Optional[str] = None,
        alg: str = "astar",
        algorithm: str = "astar",
        output_file: str = "part_bendseq.json",
        max_iterations: int = 5000,
        punch_path: Optional[str] = None,
        die_path: Optional[str] = None,
        unknown_policy: str = "PRODUCTION"
    ) -> Dict[str, Any]:
        start_time = time.time()
        diagnostics = []

        actual_alg = alg if alg != "astar" else algorithm
        
        if target_shape is None and file_path is not None:
            if file_path.lower().endswith(".fcstd"):
                from smbs_core.geometry_backend.backends.fcstd_reader import read_fcstd
                objects, _ = read_fcstd(file_path)
                if objects:
                    # Search for shape containing cylindrical bend surfaces
                    for _, shape, _ in reversed(objects):
                        try:
                            f = self.extractor.extract_features(shape)
                            if f and f.get("bends"):
                                target_shape = shape
                                break
                        except Exception:
                            continue
                    if target_shape is None:
                        target_shape = objects[-1][1]
            else:
                target_shape = self.backend.load_shape(file_path)

        if output_file == "part_bendseq.json":
            output_file = os.path.expanduser("~/part_bendseq.json")

        features = self.extractor.extract_features(target_shape)
        if not features or "bends" not in features or not features["bends"]:
            return {
                "status": "FAILED",
                "success": False,
                "error": "No valid sheet metal bend features detected in model shape.",
                "validation": {
                    "all_steps_valid": False,
                    "steps_valid": 0,
                    "steps_invalid": 0,
                    "steps_unknown": 0,
                    "tooling_alignment": AlignmentStatus.CANNOT_VERIFY.value,
                    "collision_free_confirmed": False
                },
                "diagnostics": ["No valid sheet metal bend features detected in model shape."]
            }

        bends_map = features["bends"]
        if "thickness" not in features or features["thickness"] is None:
            raise ValueError("Orchestrator: features payload missing required 'thickness'")
        try:
            thickness = float(features["thickness"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Orchestrator: 'thickness' must be numeric, got {type(features['thickness']).__name__}") from e
        if thickness <= 0.0:
            raise ValueError(f"Orchestrator: invalid non-positive thickness={thickness}")

        if "k_factor" not in features or features["k_factor"] is None:
            raise ValueError("Orchestrator: features payload missing required 'k_factor'")
        try:
            k_factor = float(features["k_factor"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Orchestrator: 'k_factor' must be numeric, got {type(features['k_factor']).__name__}") from e
        if k_factor < 0.0 or k_factor > 1.0:
            raise ValueError(f"Orchestrator: invalid k_factor={k_factor} (must be in [0.0, 1.0])")

        bg = BendGraph()
        pg = PanelGraph()
        for idx, (b_id, b_info) in enumerate(bends_map.items()):
            parent_p = b_info.get("parent_panel_id", f"PANEL_{idx:02d}")
            child_p = b_info.get("child_panel_id", f"PANEL_{(idx+1):02d}")
            rec = BendRecord(
                bend_id=b_id,
                feature_id=f"FEAT_{b_id}",
                parent_panel_id=parent_p,
                child_panel_id=child_p,
                axis_origin=tuple(b_info.get("origin", (0.0, 0.0, 0.0))),
                axis_direction=tuple(b_info.get("axis", (1.0, 0.0, 0.0))),
                signed_angle=(
                    b_info["signed_angle"] if "signed_angle" in b_info
                    else (b_info["angle"] if "angle" in b_info
                    else (b_info["signed_angle_deg"] if "signed_angle_deg" in b_info
                    else (_raise_incomplete(b_id, "signed_angle"))))
                ),
                radius=b_info.get("radius", 1.0),
                thickness=thickness,
                length=b_info.get("length", 10.0)
            )
            bg.add_bend(rec)
            pg.add_bend_connection(b_id, rec.parent_panel_id, rec.child_panel_id)

        # KL-8b: populate face_indices from B-Rep.
        # Preconditions (both required — semantic, not fallbacks):
        #   (a) target_shape is a real OCC B-Rep shape — mock/synthetic shapes
        #       in unit tests have no B-Rep faces to resolve.
        #   (b) features contains "panels" metadata — required to fit planes
        #       (normal + centroid per panel). Mock FeatureExtractor in
        #       test_bendseq / test_stage3_regression omits it by design.
        # If either precondition fails, we skip resolution entirely — no
        # silent default is applied to any panel/bend field. If we DO attempt
        # resolution, strict KeyError on pd["normal"], pd["centroid"],
        # bi["origin"], bi["child_panel_id"] surfaces contract violations
        # (invariant #3).
        # Note: production path (Coordinator -> SolverWorker) calls
        # resolve_panel_faces with real features — bypasses this guard.
        if isinstance(target_shape, TopoDS_Shape) and "panels" in features:
            canonical_payload = {
                "thickness_mm": {"value": features["thickness"]},
                "panels": {
                    pid: {
                        "normal": pd["normal"],
                        "centroid": pd["centroid"],
                    } for pid, pd in features["panels"].items()
                },
                "bends": {
                    bid: {
                        "axis_point": bi["origin"],
                        "child_panel_id": bi["child_panel_id"],
                    } for bid, bi in bends_map.items()
                },
            }
            resolve_panel_faces(target_shape, pg, canonical_payload)

        root_fold_state = FoldState.initial(target_shape, bg, pg)
        validator = PhysicalValidator(self.machine, self.backend)

        tooling_alignment = validator.alignment_status
        tooling_geo_status = validator.tooling_status

        if validator.assembly and validator.assembly.punch:
            diagnostics.extend(validator.assembly.punch.diagnostics)
        if validator.assembly and validator.assembly.die:
            diagnostics.extend(validator.assembly.die.diagnostics)

        alg = algorithm.lower()
        if alg == "dfs":
            planner = DFSBackwardPlanner(bg, validator, pg)
        elif alg == "greedy":
            planner = GreedyUnfoldPlanner(bg, validator, pg)
        else:
            planner = AStarBackwardPlanner(bg, validator, pg, state_cache=self.state_cache)

        planning_result = planner.solve(root_fold_state, max_iterations=max_iterations, unknown_policy=unknown_policy) if alg == "astar" else planner.solve(root_fold_state, unknown_policy=unknown_policy)

        if not planning_result.get("success"):
            rejections = planning_result.get("rejections", [])
            for r in rejections:
                diagnostics.append(f"Rejection [{r['bend_id']}]: stage={r['stage']} ({r['reason']})")

            return {
                "status": "FAILED",
                "success": False,
                "error": planning_result.get("error", "Solver failed to find a valid unfold path."),
                "planning_time": time.time() - start_time,
                "nodes_explored": planning_result.get("nodes_explored", 0),
                "cache_stats": self.state_cache.stats,
                "validation": {
                    "all_steps_valid": False,
                    "steps_valid": 0,
                    "steps_invalid": len(rejections),
                    "steps_unknown": 0,
                    "tooling_alignment": tooling_alignment.value,
                    "collision_free_confirmed": False
                },
                "diagnostics": diagnostics
            }

        forward_sequence = planning_result.get("sequence", [])

        detailed_sequence = []
        all_steps_valid = True
        steps_valid = 0
        steps_invalid = 0
        steps_unknown = 0
        any_unconfirmed_backgauge = False
        any_tooling_warning = False

        for step in forward_sequence:
            bend_id = step["bend_id"]
            bend_info = bends_map[bend_id]

            bg_data = self.backgauge_calculator.compute_step_backgauge(
                step_number=step["step_number"],
                bend_info=bend_info,
                part_thickness=thickness
            )

            if not bg_data.get("geometry_confirmed", False):
                any_unconfirmed_backgauge = True

            tooling = self.tool_database.select_tooling_for_bend(bend_info, thickness)
            if "warning" in tooling:
                any_tooling_warning = True
                diagnostics.append(tooling["warning"])

            step_coll_status = step.get("collision_status", "valid")
            step_tool_status = step.get("tooling_status", tooling_geo_status.value)
            step_align_status = step.get("alignment_status", tooling_alignment.value)

            if step_coll_status == "valid":
                steps_valid += 1
            elif step_coll_status == "invalid":
                steps_invalid += 1
                all_steps_valid = False
            else:
                steps_unknown += 1
                all_steps_valid = False

            step_entry = {
                "step": step["step_number"],
                "bend_id": bend_id,
                "status": "VALID" if step_coll_status == "valid" else ("INVALID" if step_coll_status == "invalid" else "UNKNOWN"),
                "collision_status": step_coll_status.upper(),
                "tooling_status": step_tool_status.upper(),
                "alignment_status": step_align_status.upper(),
                "angle_deg": (
                    step["angle"] if "angle" in step
                    else (bend_info["signed_angle"] if "signed_angle" in bend_info
                    else (bend_info["angle"] if "angle" in bend_info
                    else (_raise_incomplete(bend_id, "angle_deg"))))
                ),
                "radius_mm": step.get("radius", bend_info.get("radius", 1.0)),
                "length_mm": step.get("length", bend_info.get("length", 10.0)),
                "axis": step.get("axis", bend_info["axis"]),
                "origin": step.get("origin", bend_info["origin"]),
                "moving_panels": step.get("moving_panels", []),
                "backgauge": bg_data,
                "tooling": tooling
            }
            detailed_sequence.append(step_entry)

        optimized_sequence = self.tool_optimizer.optimize_sequence_tooling(detailed_sequence)
        total_time = time.time() - start_time

        writer = BendSeqWriter()
        written_path = writer.write_bendseq(
            filename=output_file,
            metadata={
                "part_thickness_mm": thickness,
                "k_factor": k_factor,
                "material": features.get("material", "Steel_S235"),
                "total_bends": len(bends_map),
                "algorithm_used": algorithm,
                "planning_time_sec": round(total_time, 3)
            },
            sequence=optimized_sequence
        )

        is_success = (
            all_steps_valid
            and steps_invalid == 0
            and steps_unknown == 0
            and tooling_alignment == AlignmentStatus.ALIGNED
            and not any_unconfirmed_backgauge
            and not any_tooling_warning
            and tooling_geo_status == GeometryStatus.REAL
        )

        sequence_valid = (all_steps_valid or (unknown_policy == "EXPLORATORY" and steps_invalid == 0)) and len(optimized_sequence) == len(bends_map)
        overall_status = "SUCCESS" if is_success else ("PARTIAL" if sequence_valid else "FAILED")

        validation_block = {
            "all_steps_valid": all_steps_valid,
            "steps_valid": steps_valid,
            "steps_invalid": steps_invalid,
            "steps_unknown": steps_unknown,
            "tooling_alignment": tooling_alignment.value,
            "collision_free_confirmed": is_success
        }

        return {
            "status": overall_status,
            "success": is_success,
            "sequence": optimized_sequence,
            "bendseq_file": written_path,
            "validation": validation_block,
            "planning_time": total_time,
            "nodes_explored": planning_result.get("nodes_explored", 0),
            "cache_stats": self.state_cache.stats,
            "diagnostics": diagnostics
        }
