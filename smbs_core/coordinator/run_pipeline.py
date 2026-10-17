"""
Pipeline Coordinator for SMBS / BendSeq.
Compliant with ТЗ v1.2 §4.1, §4.2, §4.5 and Principles 2, 8, 21.
Coordinates complete end-to-end pipeline:
Coordinator
 ├─ 01_input_manifest.json → Geometry/SheetMetal Worker → 02_geometry_result.json + geometry.brep
 ├─ 03_problem.json → Topology Worker → 04_canonical_part.json
 ├─ Stage 1: Baseline Worker → 04_baseline_result.json + restored.brep
 ├─ Stage 2: 06_search_request.json → Solver Worker → 07_search_result.json
 ├─ Stage 3: 08_verification_request.json → Verifier Worker → 09_verification_result.json
 └─ Export Worker → 10_part_bendseq.json + 11_run_report.json
"""

from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from smbs_core.protocols.file_store import (
    ContractError,
    FileStore,
    RunStatus,
    compute_file_sha256,
)
from smbs_core.workers.geometry.worker import GeometryWorker
from smbs_core.workers.topology.worker import TopologyWorker
from smbs_core.workers.baseline.worker import BaselineWorker
from smbs_core.workers.solver.worker import SolverWorker
from smbs_core.workers.verifier.worker import VerifierWorker
from smbs_core.workers.export.worker import ExportWorker


class Coordinator:
    """
    Coordinator orchestrating worker execution via strictly versioned file artifacts.
    Coordinator NEVER performs geometric operations or collision checks directly (§4.1).
    """

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None):
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parent.parent.parent)
        self.runs_root = self.workspace_root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.file_store = FileStore()

        # Registered workers
        self.geometry_worker = GeometryWorker(self.file_store)
        self.topology_worker = TopologyWorker(self.file_store)
        self.baseline_worker = BaselineWorker(self.file_store)
        self.solver_worker = SolverWorker(self.file_store)
        self.verifier_worker = VerifierWorker(self.file_store)
        self.export_worker = ExportWorker(self.file_store)

    def start_run(
        self,
        source_file_path: Union[str, Path],
        part_id: str,
        input_mode: str = "NATIVE_SHEETMETAL",
        target_object_name: Optional[str] = None,
        run_id: Optional[str] = None,
        comparison_mode: str = "CANONICAL",
        k_factor_override: Optional[float] = None,
        standard_override: Optional[str] = None,
        tolerance_profile: Optional[Dict[str, float]] = None,
        resource_budget: Optional[Dict[str, Any]] = None,
        search_algorithm: str = "astar",
        machine_config: Optional[Dict[str, Any]] = None,
        unknown_policy: str = "PRODUCTION",
    ) -> Dict[str, Any]:
        """
        Executes full pipeline:
        Stage 1: Geometry -> Topology -> Baseline.
        Stage 2: Search Request -> Solver -> Search Result.
        Stage 3: Verification Request -> Verifier -> Export Worker.
        Returns final verified part_bendseq report.
        """
        source_path = Path(source_file_path)
        if not source_path.is_absolute():
            source_path = self.workspace_root / source_path

        if not source_path.exists():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")

        run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        run_dir = self.runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        input_fingerprint = compute_file_sha256(source_path)

        # 0. Write 01_input_manifest.json
        manifest_data = {
            "schema_name": "input_manifest",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "coordinator", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value,
            "diagnostics": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "source_file_path": str(source_path),
                "input_mode": input_mode,
                "target_object_name": target_object_name,
                "comparison_mode": comparison_mode,
                "k_factor_override": k_factor_override,
                "standard_override": standard_override,
                "linear_tolerance_mm": 0.1,
                "angular_tolerance_deg": 0.5
            }
        }

        manifest_file = run_dir / "01_input_manifest.json"
        self.file_store.write_json_atomic(manifest_file, manifest_data, schema_name="input_manifest")

        # 1. Geometry Worker
        geom_result_file = self.geometry_worker.execute(manifest_file)
        geom_result = self.file_store.read_json_verified(geom_result_file, expected_schema_name="geometry_result")

        # Verify geometry.brep exists and hash matches
        geom_artifact_info = geom_result["payload"]["geometry_artifact"]
        brep_file = self.workspace_root / geom_artifact_info["file_path"]
        if not brep_file.exists():
            brep_file = run_dir / Path(geom_artifact_info["file_path"]).name
            if not brep_file.exists():
                raise ContractError(f"Worker promised geometry.brep at {brep_file}, but file not found.")

        actual_brep_hash = compute_file_sha256(brep_file)
        if actual_brep_hash != geom_artifact_info["content_hash"]:
            raise ContractError(
                f"Binary integrity mismatch: promised {geom_artifact_info['content_hash']} != actual {actual_brep_hash}"
            )

        # 2. Topology Worker (§4.1, 04_canonical_part.json)
        canonical_part_file = self.topology_worker.execute(geom_result_file)
        canonical_part = self.file_store.read_json_verified(canonical_part_file, expected_schema_name="canonical_part")

        # 3. Stage 1 Baseline Worker (§5.1, unfold -> align -> fold -> compare)
        baseline_result_file = self.baseline_worker.execute(
            geom_result_file,
            canonical_part_file,
            tolerance_profile=tolerance_profile,
            comparison_mode=comparison_mode
        )
        baseline_result = self.file_store.read_json_verified(baseline_result_file, expected_schema_name="baseline_result")

        # If Baseline fails, abort search according to §1.3
        if not baseline_result["payload"]["pass"]:
            return {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "status": RunStatus.WORKER_ERROR.value,
                "stage": "baseline",
                "baseline_status": baseline_result["payload"]["baseline_status"],
                "pass": False,
                "diagnostics": baseline_result["diagnostics"]
            }

        # 4. Stage 2: Create Tooling Manifest & 06_search_request.json
        tooling_diagnostics = []
        if machine_config:
            punch_path = machine_config.get("punch_path")
            die_path = machine_config.get("die_path")
            punch_id = Path(punch_path).stem if punch_path else "punch_real"
            die_id = Path(die_path).stem if die_path else "die_real"
            press_id = machine_config.get("press_id", "press_100t")
            bed_length = float(machine_config.get("bed_length_mm", 3000.0))

            payload_punch = {
                "tool_id": punch_id,
                "punch_type": "straight",
                "height_mm": 100.0,
                "tip_radius_mm": 1.0,
                "tip_angle_deg": 88.0,
                "tooling_status": "REAL",
                "model_path": str(punch_path) if punch_path else None,
                "model_hash": None,
                "segment_layout_mm": [100.0, 200.0]
            }
            if "punch_target_object_name" in machine_config:
                payload_punch["target_object_name"] = machine_config["punch_target_object_name"]

            payload_die = {
                "tool_id": die_id,
                "v_opening_mm": 12.0,
                "v_angle_deg": 88.0,
                "height_mm": 80.0,
                "shoulder_radius_mm": 1.0,
                "tooling_status": "REAL",
                "model_path": str(die_path) if die_path else None,
                "model_hash": None,
                "segment_layout_mm": [100.0, 200.0]
            }
            if "die_target_object_name" in machine_config:
                payload_die["target_object_name"] = machine_config["die_target_object_name"]

            payload_press = {
                "press_id": press_id,
                "max_tonnage_tons": float(machine_config.get("max_tonnage_tons", 100.0)),
                "bed_length_mm": bed_length,
                "stroke_mm": float(machine_config.get("stroke_mm", 200.0)),
                "open_height_mm": float(machine_config.get("open_height_mm", 350.0))
            }
        else:
            tooling_diagnostics.append("machine_config not provided; default SIMPLIFIED tooling")
            payload_press = {
                "press_id": "press_100t",
                "max_tonnage_tons": 100.0,
                "bed_length_mm": 3000.0,
                "stroke_mm": 200.0,
                "open_height_mm": 350.0
            }
            payload_punch = {
                "tool_id": "PUNCH_STD_R1",
                "punch_type": "straight",
                "height_mm": 100.0,
                "tip_radius_mm": 1.0,
                "tip_angle_deg": 88.0,
                "tooling_status": "SIMPLIFIED",
                "segment_layout_mm": [100.0, 200.0]
            }
            payload_die = {
                "tool_id": "DIE_V12",
                "v_opening_mm": 12.0,
                "v_angle_deg": 88.0,
                "height_mm": 80.0,
                "shoulder_radius_mm": 1.0,
                "tooling_status": "SIMPLIFIED",
                "segment_layout_mm": [100.0, 200.0]
            }

        tooling_manifest_data = {
            "schema_name": "tooling_manifest",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "coordinator", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value,
            "diagnostics": tooling_diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "press": payload_press,
                "punch": payload_punch,
                "die": payload_die
            }
        }
        tooling_file = run_dir / "tooling_manifest.json"
        tooling_hash = self.file_store.write_json_atomic(tooling_file, tooling_manifest_data, schema_name="tooling_manifest")

        canonical_part_hash = canonical_part["content_hash"]
        budget = resource_budget or {
            "max_wall_time_s": 30.0,
            "max_nodes": 50000,
            "max_cache_entries": 20000,
            "max_geometry_ops": 5000,
            "max_trajectory_samples": 2000,
            "max_memory_mb": 2048.0
        }

        search_req_data = {
            "schema_name": "search_request",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "coordinator", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value,
            "diagnostics": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "canonical_part_ref": {
                    "file_path": str(canonical_part_file.relative_to(self.workspace_root)),
                    "content_hash": canonical_part_hash
                },
                "tooling_manifest_ref": {
                    "file_path": str(tooling_file.relative_to(self.workspace_root)),
                    "content_hash": tooling_hash
                },
                "search_algorithm": search_algorithm,
                "candidate_order_policy": "outside_in",
                "unknown_policy": unknown_policy,
                "cost_weights": {"w_tool": 100.0, "w_flip": 20.0, "w_setup": 10.0, "w_time": 1.0},
                "resource_budget": budget
            }
        }
        search_req_file = run_dir / "06_search_request.json"
        self.file_store.write_json_atomic(search_req_file, search_req_data, schema_name="search_request")

        # 5. Invoke Solver Worker
        search_result_file = self.solver_worker.execute(search_req_file)
        search_result = self.file_store.read_json_verified(search_result_file, expected_schema_name="search_result")

        # 6. Stage 3: Invoke Independent Verifier Worker
        verif_result_file = self.verifier_worker.execute(
            search_result_file,
            canonical_part_file,
            tolerance_profile=tolerance_profile
        )
        verif_result = self.file_store.read_json_verified(verif_result_file, expected_schema_name="verification_result")

        # 7. Invoke Export Worker (§7.2, §7.3)
        export_file, report_file = self.export_worker.execute(
            search_result_file,
            verif_result_file,
            canonical_part_file
        )
        part_bendseq = self.file_store.read_json_verified(export_file, expected_schema_name="part_bendseq")
        run_report = self.file_store.read_json_verified(report_file, expected_schema_name="envelope")

        return {
            "status": RunStatus.SUCCESS.value if part_bendseq["production_eligible"] else part_bendseq["planning_status"],
            "run_id": run_id,
            "run_dir": str(run_dir),
            "manifest_path": str(manifest_file),
            "geometry_result_path": str(geom_result_file),
            "canonical_part_path": str(canonical_part_file),
            "baseline_result_path": str(baseline_result_file),
            "search_result_path": str(search_result_file),
            "verification_result_path": str(verif_result_file),
            "export_file_path": str(export_file),
            "run_report_path": str(report_file),
            "planning_status": part_bendseq["planning_status"],
            "verification_status": part_bendseq["verification_status"],
            "export_status": part_bendseq["export_status"],
            "production_eligible": part_bendseq["production_eligible"],
            "sequence": part_bendseq["sequence"],
            "baseline_status": baseline_result["payload"]["baseline_status"],
            "pass": baseline_result["payload"]["pass"],
            "baseline_metrics": baseline_result["payload"]["metrics"],
            "payload": geom_result["payload"],
            "canonical_part": canonical_part["payload"],
            "run_report": run_report["payload"]
        }
