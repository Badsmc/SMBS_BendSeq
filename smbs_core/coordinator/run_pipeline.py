"""
Pipeline Coordinator for SMBS / BendSeq.
Compliant with ТЗ v1.2 §4.1, §4.2, §4.5 and Principle 2.
Coordinates file exchange between isolated workers across runs/<run_id>/ directory.
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

    def start_run(
        self,
        source_file_path: Union[str, Path],
        part_id: str,
        input_mode: str = "NATIVE_SHEETMETAL",
        run_id: Optional[str] = None,
        k_factor_override: Optional[float] = None,
        standard_override: Optional[str] = None,
        tolerance_profile: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Initializes runs/<run_id>/, writes 01_input_manifest.json and executes Stage 1 (Geometry -> Topology -> Baseline).
        Returns final verified run status, paths and baseline report.
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
                "target_object_name": None,
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
            tolerance_profile=tolerance_profile
        )
        baseline_result = self.file_store.read_json_verified(baseline_result_file, expected_schema_name="baseline_result")

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "manifest_path": str(manifest_file),
            "geometry_result_path": str(geom_result_file),
            "geometry_brep_path": str(brep_file),
            "canonical_part_path": str(canonical_part_file),
            "baseline_result_path": str(baseline_result_file),
            "status": baseline_result["status"],
            "baseline_status": baseline_result["payload"]["baseline_status"],
            "pass": baseline_result["payload"]["pass"],
            "diagnostics": baseline_result["diagnostics"],
            "payload": geom_result["payload"],
            "canonical_part": canonical_part["payload"],
            "baseline_metrics": baseline_result["payload"]["metrics"]
        }
