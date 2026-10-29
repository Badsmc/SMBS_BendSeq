"""
manual_worker.py - Phase 4a Autonomous Mini-Pipeline & Canonical Resolution.

Implements:
- ManualSequenceInput & ManualSequenceResult (Option B, Decision B4/B5).
- Autonomous staged execution:
    Stage 1: 01_input_manifest.json + GeometryWorker -> 02_geometry_result.json + BREPs
    Stage 2: TopologyWorker -> 04_canonical_part.json
    Stage 3: match_to_canonical resolution with fail-fast validation.
- Output artifacts compatibility adapter (to_pipeline_run_result).
- Cancellation support (threading.Event) (Y4).
- Configurable run directory policy (B4).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from freecad_smbs.integration.smbs_bridge import PipelineRunResult, RunArtifacts, load_run
from freecad_smbs.manual.geometry_extract import (
    BendLine,
    match_to_canonical,
)
from freecad_smbs.ui.manual_sequence_panel import (
    BendSelectionItem,
    RawBendLineSnapshot,
)
from smbs_core.protocols.file_store import (
    ContractError,
    FileStore,
    RunStatus,
    compute_file_sha256,
)
from smbs_core.workers.geometry.worker import GeometryWorker
from smbs_core.workers.topology.worker import TopologyWorker

logger = logging.getLogger("smbs.manual_worker")


@dataclass
class ManualSequenceInput:
    """Input payload for manual sequence execution."""
    selections: List[BendSelectionItem]
    tooling_set_id: Optional[str] = None
    unknown_policy: str = "EXPLORATORY"  # "EXPLORATORY" (default) or "PRODUCTION"
    part_id: Optional[str] = None
    cancel_token: Optional[threading.Event] = None



@dataclass
class ManualSequenceResult:
    """Structured result of executing manual sequence on FCStd model."""
    ok: bool
    run_dir: Path
    part_id: str
    selections: List[BendSelectionItem] = field(default_factory=list)
    unmatched_snapshots: List[RawBendLineSnapshot] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    planning_status: str = "UNKNOWN"  # "SUCCESS", "NO_SOLUTION", "ERROR", "CANCELLED"
    error_message: Optional[str] = None
    diagnostics: List[str] = field(default_factory=list)
    artifacts: Optional[RunArtifacts] = None

    def to_pipeline_run_result(self) -> PipelineRunResult:
        """Adapts result for downstream UI components (Verifier, Animation dialogs)."""
        candidate_seq: List[Dict[str, Any]] = []
        for sel in self.selections:
            if sel.bend_id:
                candidate_seq.append({
                    "step_number": sel.order,
                    "bend_id": sel.bend_id,
                    "edge_ref": sel.edge_ref,
                })

        return PipelineRunResult(
            run_dir=self.run_dir,
            planning_status=self.planning_status,
            candidate_sequence=candidate_seq,
            part_id=self.part_id,
            error_message=self.error_message,
            diagnostics=self.diagnostics,
            artifacts=self.artifacts,
            status="SUCCESS" if self.ok else "ERROR",
        )


class ManualSequenceWorker:
    """Coordinates autonomous mini-pipeline and canonical bend resolution (Phase 4a)."""

    def __init__(self, workspace_root: Optional[Path] = None, file_store: Optional[FileStore] = None):
        self.workspace_root = (workspace_root or Path(__file__).resolve().parents[2]).resolve()
        self.file_store = file_store or FileStore()
        self.geometry_worker = GeometryWorker(self.file_store)
        self.topology_worker = TopologyWorker(self.file_store)

    def execute_mini_pipeline(
        self,
        fcstd_path: Union[str, Path],
        manual_input: ManualSequenceInput,
        *,
        out_runs_dir: Union[str, Path, None] = None,
        cancel_token: Optional[threading.Event] = None,
        phases: Optional[List[str]] = None,
    ) -> ManualSequenceResult:
        """
        Executes Stages 1-3:
        1. 01_input_manifest.json + GeometryWorker -> 02_geometry_result.json + geometry.brep + flat_pattern.brep
        2. TopologyWorker -> 04_canonical_part.json
        3. Match selections to canonical 04; fail-closed if any snapshot fails to resolve.
        """
        token = cancel_token or manual_input.cancel_token
        source_path = Path(fcstd_path).expanduser().resolve()
        part_id = manual_input.part_id or source_path.stem

        if not source_path.is_file():
            return ManualSequenceResult(
                ok=False,
                run_dir=Path(),
                part_id=part_id,
                planning_status="ERROR",
                error_message=f"FCStd file not found: {source_path}",
                diagnostics=[f"File not found: {source_path}"],
            )

        # 1. Determine run directory
        run_id = f"run_manual_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        if out_runs_dir is not None:
            runs_root = Path(out_runs_dir).expanduser().resolve()
        else:
            runs_root = self.workspace_root / "runs"
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if token and token.is_set():
            return ManualSequenceResult(
                ok=False,
                run_dir=run_dir,
                part_id=part_id,
                planning_status="CANCELLED",
                error_message="Operation cancelled by user",
                diagnostics=["Cancelled prior to pipeline start"],
            )

        # 2. Stage 1: Input Manifest & GeometryWorker
        try:
            input_fingerprint = compute_file_sha256(source_path)
            manifest_data = {
                "schema_name": "input_manifest",
                "schema_version": "1.1",
                "run_id": run_id,
                "part_id": part_id,
                "input_fingerprint": input_fingerprint,
                "producer": {"name": "manual_worker", "version": "1.1"},
                "units": "mm",
                "frame_convention": "STANDARD_CARTESIAN_MM",
                "status": RunStatus.SUCCESS.value,
                "diagnostics": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "source_file_path": str(source_path),
                    "input_mode": "NATIVE_SHEETMETAL",
                    "target_object_name": None,
                    "comparison_mode": "CANONICAL",
                    "k_factor_override": None,
                    "standard_override": None,
                    "linear_tolerance_mm": 0.1,
                    "angular_tolerance_deg": 0.5,
                },
            }
            manifest_file = run_dir / "01_input_manifest.json"
            self.file_store.write_json_atomic(manifest_file, manifest_data, schema_name="input_manifest")

            geom_result_file = self.geometry_worker.execute(manifest_file)
            geom_result = self.file_store.read_json_verified(geom_result_file, expected_schema_name="geometry_result")
        except Exception as e:
            return ManualSequenceResult(
                ok=False,
                run_dir=run_dir,
                part_id=part_id,
                planning_status="ERROR",
                error_message=f"GeometryWorker failed: {e}",
                diagnostics=[str(e)],
            )

        if token and token.is_set():
            return ManualSequenceResult(
                ok=False,
                run_dir=run_dir,
                part_id=part_id,
                planning_status="CANCELLED",
                error_message="Operation cancelled after geometry extraction",
                diagnostics=["Cancelled after GeometryWorker"],
            )

        # 3. Stage 2: TopologyWorker
        try:
            canonical_part_file = self.topology_worker.execute(geom_result_file)
            canonical_part = self.file_store.read_json_verified(canonical_part_file, expected_schema_name="canonical_part")
        except Exception as e:
            return ManualSequenceResult(
                ok=False,
                run_dir=run_dir,
                part_id=part_id,
                planning_status="ERROR",
                error_message=f"TopologyWorker failed: {e}",
                diagnostics=[str(e)],
            )

        # 4. Stage 3: Match user selections to canonical 04
        unmatched: List[RawBendLineSnapshot] = []
        resolved_selections: List[BendSelectionItem] = []

        # Prepare raw items for match_to_canonical
        snapshots = [sel.raw_snapshot for sel in manual_input.selections]
        matched_bends: List[BendLine] = match_to_canonical(snapshots, canonical_part)
        matched_map: Dict[Tuple[float, float, float], BendLine] = {
            tuple(round(c, 3) for c in b.midpoint): b for b in matched_bends
        }

        for sel in manual_input.selections:
            key = tuple(round(c, 3) for c in sel.raw_snapshot.midpoint)
            matched_b = matched_map.get(key)
            if matched_b is not None:
                resolved_item = BendSelectionItem(
                    order=sel.order,
                    edge_ref=sel.edge_ref,
                    object_name=sel.object_name,
                    raw_snapshot=sel.raw_snapshot,
                    bend_id=matched_b.bend_id,
                )
                resolved_selections.append(resolved_item)
            else:
                unmatched.append(sel.raw_snapshot)

        if unmatched or len(resolved_selections) != len(manual_input.selections):
            diag = f"Failed to match {len(unmatched)} bend snapshot(s) against canonical part 04."
            return ManualSequenceResult(
                ok=False,
                run_dir=run_dir,
                part_id=part_id,
                selections=resolved_selections,
                unmatched_snapshots=unmatched,
                planning_status="ERROR",
                error_message=diag,
                diagnostics=[diag],
            )

        # Try loading artifacts inventory
        artifacts: Optional[RunArtifacts] = None
        try:
            artifacts = load_run(run_dir)
        except Exception:
            pass

        return ManualSequenceResult(
            ok=True,
            run_dir=run_dir,
            part_id=part_id,
            selections=resolved_selections,
            unmatched_snapshots=[],
            planning_status="SUCCESS",
            artifacts=artifacts,
        )
