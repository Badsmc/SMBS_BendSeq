"""
Solver Worker for SMBS / BendSeq (ТЗ v1.2 §6).
Implements deterministic search with ResourceBudget, unknown_policy, and full metrics tracking.
Reads 06_search_request.json -> writes 07_search_result.json.
"""

from __future__ import annotations
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder

from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.state_cache import StateCache
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.Physics.backgauge_calculator import BackgaugeCalculator
from smbs_core.Solvers.astar_backward import AStarBackwardPlanner
from smbs_core.Solvers.dfs_backward import DFSBackwardPlanner
from smbs_core.Solvers.greedy_unfold import GreedyUnfoldPlanner
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.protocols.file_store import (
    ConfidenceLevel,
    ContractError,
    FileStore,
    ParameterSource,
    RunStatus,
    ToolingStatus,
    TransitionStatus,
    AlignmentStatus,
)


class SolverWorker:
    """
    Solver Worker executing backward search under explicit ResourceBudget (§6.1, §6.5).
    Produces candidate sequence for subsequent independent verification.
    """

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()
        self.backend = PythonOCCBackend()
        self.machine = PressBrakeMachine()
        self.backgauge_calc = BackgaugeCalculator(self.machine)

    def _load_brep(self, file_path: Union[str, Path]) -> TopoDS_Shape:
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not breptools.Read(shape, str(file_path), builder):
            raise ValueError(f"Failed to load BREP from {file_path}")
        return shape

    def execute(self, search_request_path: Union[str, Path]) -> Path:
        """
        Executes solver on 06_search_request.json.
        Writes 07_search_result.json.
        """
        req = self.file_store.read_json_verified(search_request_path, expected_schema_name="search_request")

        run_id = req["run_id"]
        part_id = req["part_id"]
        input_fingerprint = req["input_fingerprint"]
        payload = req["payload"]
        run_dir = Path(search_request_path).parent
        workspace_root = run_dir.parent.parent

        # 1. Load canonical part
        part_ref = payload["canonical_part_ref"]["file_path"]
        part_file = workspace_root / part_ref
        if not part_file.exists():
            part_file = run_dir / Path(part_ref).name
        canonical_part = self.file_store.read_json_verified(part_file, expected_schema_name="canonical_part")

        # 2. Load geometry artifact
        geom_artifact_rel = canonical_part["payload"]["geometry_artifact"]["file_path"]
        brep_path = workspace_root / geom_artifact_rel
        if not brep_path.exists():
            brep_path = run_dir / Path(geom_artifact_rel).name
        occ_shape = self._load_brep(brep_path)

        # 3. Assemble graphs from canonical part
        panels_dict = canonical_part["payload"]["panels"]
        bends_dict = canonical_part["payload"]["bends"]

        panel_graph = PanelGraph()
        for pid, pdata in panels_dict.items():
            panel_graph.add_panel(Panel(pid, normal=tuple(pdata.get("normal", (0.0, 0.0, 1.0)))))

        for bid, bdata in bends_dict.items():
            panel_graph.add_bend_connection(bid, bdata["parent_panel_id"], bdata["child_panel_id"])

        bend_records: Dict[str, BendRecord] = {}
        bend_axes: Dict[str, Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = {}
        bend_transforms: Dict[str, float] = {}

        for bid, bdata in bends_dict.items():
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=bdata["parent_panel_id"],
                child_panel_id=bdata["child_panel_id"],
                axis_origin=tuple(bdata["axis_point"]),
                axis_direction=tuple(bdata["axis_direction"]),
                signed_angle=bdata["angle_deg"]["value"],
                length=bdata["length_mm"]["value"],
                radius=bdata["radius_mm"]["value"],
                thickness=canonical_part["payload"]["thickness_mm"]["value"]
            )
            bend_records[bid] = rec
            bend_axes[bid] = (tuple(bdata["axis_point"]), tuple(bdata["axis_direction"]))
            bend_transforms[bid] = bdata["angle_deg"]["value"]

        bend_graph = BendGraph()
        for rec in bend_records.values():
            bend_graph.add_bend(rec)

        # 4. Construct initial FoldState
        initial_state = FoldState.initial(
            part_shape=occ_shape,
            bend_graph=bend_graph,
            panel_graph=panel_graph
        )

        # 5. Configure PhysicalValidator & Solver
        diagnostics = []

        # Tooling manifest & machine setup (items 2, 5, 7)
        tooling_manifest_ref = payload.get("tooling_manifest_ref", {})
        tooling_manifest_file_rel = tooling_manifest_ref.get("file_path") if isinstance(tooling_manifest_ref, dict) else None
        tooling_manifest = None
        if tooling_manifest_file_rel:
            tooling_path = workspace_root / tooling_manifest_file_rel
            if not tooling_path.exists():
                tooling_path = run_dir / Path(tooling_manifest_file_rel).name
            if tooling_path.exists():
                try:
                    tooling_manifest = self.file_store.read_json_verified(tooling_path, expected_schema_name="tooling_manifest")
                except Exception as e:
                    diagnostics.append(f"tooling_manifest: failed to load ({e})")

        machine_config = payload.get("machine_config")
        punch_path = payload.get("punch_path")
        die_path = payload.get("die_path")

        if machine_config or punch_path or die_path:
            cfg = machine_config.copy() if machine_config else {}
            if punch_path:
                cfg["punch_path"] = punch_path
            if die_path:
                cfg["die_path"] = die_path
            machine = PressBrakeMachine.from_config(cfg)
        elif tooling_manifest and "payload" in tooling_manifest:
            tm_payload = tooling_manifest["payload"]
            punch_cfg = tm_payload.get("punch", {})
            die_cfg = tm_payload.get("die", {})
            press_cfg = tm_payload.get("press", {})
            machine = PressBrakeMachine(
                name=press_cfg.get("press_id", "press_100t"),
                bed_length_mm=press_cfg.get("bed_length_mm", 2000.0),
                punch_path=punch_cfg.get("model_path"),
                die_path=die_cfg.get("model_path")
            )
            if not punch_cfg.get("model_path") or not die_cfg.get("model_path"):
                diagnostics.append("machine: default synthetic (SIMPLIFIED tooling)")
        else:
            machine = PressBrakeMachine()
            diagnostics.append("machine: default synthetic (SIMPLIFIED tooling)")

        validator = PhysicalValidator(machine, self.backend)
        state_cache = StateCache()

        budget = payload["resource_budget"]
        algorithm = payload.get("search_algorithm", "astar").lower()
        unknown_policy = payload.get("unknown_policy", "PRODUCTION")

        start_time = time.time()

        timeout_s = budget.get("max_wall_time_s", 30.0)
        max_nodes = budget.get("max_nodes", 50000)

        if algorithm == "dfs":
            planner = DFSBackwardPlanner(bend_graph, validator, panel_graph, state_cache)
            res = planner.solve(initial_state, max_depth=max_nodes, timeout=timeout_s)
        elif algorithm == "greedy":
            planner = GreedyUnfoldPlanner(bend_graph, validator, panel_graph, state_cache)
            res = planner.solve(initial_state, max_iterations=max_nodes, timeout=timeout_s)
        elif algorithm == "astar":
            planner = AStarBackwardPlanner(bend_graph, validator, panel_graph, state_cache)
            res = planner.solve(initial_state, max_iterations=max_nodes, timeout=timeout_s)
        else:
            raise NotImplementedError(f"Unsupported search algorithm: {algorithm}")

        wall_time = time.time() - start_time

        # Check budget limits and planner termination reason (§6.5)
        res_reason = res.get("termination_reason")
        if wall_time > budget.get("max_wall_time_s", 30.0) or res_reason == "TIME_EXHAUSTED":
            planning_status = RunStatus.TIMEOUT
            termination_reason = "TIME_EXHAUSTED"
        elif res.get("success"):
            planning_status = RunStatus.SUCCESS
            termination_reason = "GOAL_REACHED"
        elif res_reason in ("RESOURCE_EXHAUSTED", "NODE_LIMIT_EXHAUSTED", "RESOURCE_LIMIT_EXHAUSTED"):
            planning_status = RunStatus.RESOURCE_EXHAUSTED
            termination_reason = "NODE_LIMIT_EXHAUSTED"
        else:
            planning_status = RunStatus.NO_SOLUTION
            termination_reason = "NO_MORE_CANDIDATES"

        # 6. Format Candidate Sequence
        candidate_sequence = []
        raw_seq = res.get("sequence", [])

        # Tooling status from validator assembly
        actual_tooling_status = validator.tooling_status.value if hasattr(validator.tooling_status, "value") else str(validator.tooling_status)
        if actual_tooling_status not in ("REAL", "SIMPLIFIED", "MISSING"):
            actual_tooling_status = "SIMPLIFIED"

        # Punch ID from tooling manifest if present
        default_tool_id = "UNKNOWN"
        if tooling_manifest and "payload" in tooling_manifest and "punch" in tooling_manifest["payload"]:
            default_tool_id = tooling_manifest["payload"]["punch"].get("tool_id", "UNKNOWN")

        has_unknown_step = False
        unknown_abort_steps = []

        backgauge_calc = BackgaugeCalculator(machine)

        for idx, step_item in enumerate(raw_seq):
            bid = step_item["bend_id"]
            bend_def = bends_dict[bid]

            # Backgauge calculation (Item 4)
            bend_info_for_bg = {
                "length": bend_def["length_mm"]["value"],
                "bend_id": bid,
                "geometry_confirmed": False
            }
            bg_res = backgauge_calc.compute_step_backgauge(
                step_number=idx + 1,
                bend_info=bend_info_for_bg,
                part_thickness=canonical_part["payload"]["thickness_mm"]["value"],
                confirmed_geometry=False
            )

            if bg_res.get("geometry_confirmed", False):
                bg_data = {
                    "x_mm": round(bg_res["X_mm"], 2),
                    "r_mm": round(bg_res["R_mm"], 2),
                    "z1_mm": round(bg_res["Z1_mm"], 2),
                    "z2_mm": round(bg_res["Z2_mm"], 2),
                    "confidence": ConfidenceLevel.CONFIRMED.value
                }
            else:
                bg_data = {
                    "x_mm": None,
                    "r_mm": None,
                    "z1_mm": None,
                    "z2_mm": None,
                    "confidence": ConfidenceLevel.UNKNOWN.value
                }

            # tool_id and tooling_status (Items 5, 7)
            step_tool_id = step_item.get("tool_id") or default_tool_id
            step_tooling_status = step_item.get("tooling_status") or actual_tooling_status
            if step_tool_id == "UNKNOWN" and not tooling_manifest:
                step_tooling_status = "MISSING"

            # collision_status (Item 6)
            step_collision_status = step_item.get("collision_status", TransitionStatus.UNKNOWN.value)
            if hasattr(step_collision_status, "value"):
                step_collision_status = step_collision_status.value
            step_collision_status = str(step_collision_status).upper()
            if step_collision_status not in ("VALID", "INVALID", "UNKNOWN", "INSUFFICIENT_CLEARANCE"):
                step_collision_status = TransitionStatus.UNKNOWN.value

            # planner_status
            step_planner_status = step_item.get("planner_status", TransitionStatus.VALID.value)
            if hasattr(step_planner_status, "value"):
                step_planner_status = step_planner_status.value
            step_planner_status = str(step_planner_status).upper()
            if step_planner_status not in ("VALID", "INVALID", "UNKNOWN"):
                step_planner_status = TransitionStatus.UNKNOWN.value

            # alignment_status (Item 8)
            step_alignment_status = step_item.get("alignment_status")
            if step_alignment_status is None:
                step_alignment_status = validator.alignment_status.value if hasattr(validator.alignment_status, "value") else str(validator.alignment_status)
            elif hasattr(step_alignment_status, "value"):
                step_alignment_status = step_alignment_status.value
            step_alignment_status = str(step_alignment_status).upper()
            if step_alignment_status not in ("OK", "CANNOT_VERIFY", "FRAME_AMBIGUOUS"):
                step_alignment_status = AlignmentStatus.CANNOT_VERIFY.value

            if step_collision_status == TransitionStatus.UNKNOWN.value or step_planner_status == TransitionStatus.UNKNOWN.value:
                has_unknown_step = True
                unknown_abort_steps.append((idx + 1, bid, step_collision_status))

            step_data = {
                "step": idx + 1,
                "bend_id": bid,
                "direction": bend_def["direction"],
                "angle_deg": bend_def["angle_deg"],
                "radius_mm": bend_def["radius_mm"],
                "k_factor": bend_def["k_factor"],
                "backgauge": bg_data,
                "tool_id": step_tool_id,
                "planner_status": step_planner_status,
                "collision_status": step_collision_status,
                "tooling_status": step_tooling_status,
                "alignment_status": step_alignment_status
            }
            candidate_sequence.append(step_data)

        # 7. Collect solver metrics (§6.2, Item 9)
        prefilter_checks = res.get("prefilter_checks", 0)
        rejections_count = len(res.get("rejections", []))
        exact_collision_queries = res.get("exact_collision_queries", rejections_count + len(candidate_sequence))
        trajectory_samples = res.get("trajectory_samples", 0)
        solver_nodes = res.get("nodes_explored") or res.get("explored_count", 0)

        # Peak memory measurement
        try:
            import resource
            import sys
            rusage_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                measured_peak_mb = round(rusage_rss / (1024.0 * 1024.0), 2)
            else:
                measured_peak_mb = round(rusage_rss / 1024.0, 2)
        except Exception:
            measured_peak_mb = 0.0

        solver_metrics = {
            "prefilter_checks": int(prefilter_checks),
            "exact_collision_queries": int(exact_collision_queries),
            "trajectory_samples": int(trajectory_samples),
            "solver_nodes": int(solver_nodes),
            "wall_time_s": round(wall_time, 4),
            "peak_memory_mb": measured_peak_mb
        }

        # 8. Compute total_cost from cost_weights (Items 3, 11)
        cost_weights = payload.get("cost_weights", {})
        w_tool = float(cost_weights.get("w_tool", 100.0))
        w_flip = float(cost_weights.get("w_flip", 20.0))
        w_setup = float(cost_weights.get("w_setup", 10.0))
        w_time = float(cost_weights.get("w_time", 1.0))

        # Calculate flips and tool changes from candidate sequence
        flips = 0
        tool_changes = 0
        prev_dir = None
        prev_tool = None
        for step in candidate_sequence:
            cur_dir = step["direction"].get("value") if isinstance(step["direction"], dict) else str(step["direction"])
            cur_tool = step["tool_id"]
            if prev_dir is not None and cur_dir != prev_dir:
                flips += 1
            if prev_tool is not None and cur_tool != prev_tool:
                tool_changes += 1
            prev_dir = cur_dir
            prev_tool = cur_tool

        total_cost = round(w_tool * tool_changes + w_flip * flips + w_setup * tool_changes + w_time * wall_time, 4)

        # 9. Optimality claim & unknown_policy resolution (Items 10, 12)
        if planning_status == RunStatus.SUCCESS:
            if has_unknown_step:
                if unknown_policy == "PRODUCTION":
                    planning_status = RunStatus.NO_SOLUTION
                    termination_reason = "UNKNOWN_ABORT"
                    optimality_claim = "feasible_unoptimized"
                    for s_num, s_bid, s_cstat in unknown_abort_steps:
                        diagnostics.append(f"UNKNOWN_ABORT: step {s_num} (bend {s_bid}) collision_status={s_cstat} under PRODUCTION policy")
                else:  # EXPLORATORY
                    planning_status = RunStatus.PARTIAL
                    optimality_claim = "feasible_unoptimized"
            else:
                planner_opt = res.get("optimality")
                if planner_opt == "optimal":
                    optimality_claim = "optimal"
                else:
                    optimality_claim = "best_found_under_budget"
        else:
            optimality_claim = "feasible_unoptimized"

        search_result_payload = {
            "schema_name": "search_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "solver_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": planning_status.value,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "planning_status": planning_status.value,
                "termination_reason": termination_reason,
                "optimality_claim": optimality_claim,
                "total_cost": total_cost,
                "candidate_sequence": candidate_sequence,
                "solver_metrics": solver_metrics
            }
        }

        out_file = run_dir / "07_search_result.json"
        self.file_store.write_json_atomic(out_file, search_result_payload, schema_name="search_result")
        return out_file
