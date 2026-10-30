"""
smbs_bridge.py - Read-only adapter bridge between freecad_smbs and smbs_core.

Enforces strict read-only access to run artifacts without mutating smbs_core/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from freecad_smbs.ui.badges import (
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
    get_badge_hex,
    get_badge_rgba,
)
from smbs_core.viewer.flat_annotations import (
    BendAnnotation,
    FlatAnnotationsError,
    compute_annotations,
)
from smbs_core.viewer.graph_builder import build_graphs
from smbs_core.viewer.run_loader import RunArtifacts, load_run as _core_load_run


def load_run(run_dir: Union[str, Path]) -> RunArtifacts:
    """Load and parse run artifacts strictly in read-only mode."""
    path = Path(run_dir).expanduser().resolve()
    return _core_load_run(path)


def get_annotations(artifacts: RunArtifacts) -> List[BendAnnotation]:
    """Compute bend annotations (read-only). Raises FlatAnnotationsError on invalid run."""
    graphs = build_graphs(artifacts)
    return compute_annotations(artifacts, graphs)


def present_info(artifacts: RunArtifacts) -> Dict[str, Any]:
    """Extract human-readable metadata, artifacts inventory, and known limitations."""
    candidate_seq: List[Dict[str, Any]] = []
    if artifacts.search_result and "payload" in artifacts.search_result:
        candidate_seq = artifacts.search_result["payload"].get("candidate_sequence", [])

    bend_ids: List[str] = [
        step.get("bend_id", f"step_{i+1}")
        for i, step in enumerate(candidate_seq)
        if isinstance(step, dict)
    ]

    has_geom = artifacts.geometry_shape_path is not None and artifacts.geometry_shape_path.exists()
    has_flat = artifacts.flat_pattern_path is not None and artifacts.flat_pattern_path.exists()

    limitations = [
        "KL-12: N≥3 complex tree branches remain PREVIEW_ONLY (N≤2 linear chains yield VALID_PER_PANEL).",
        "KL-16: machine frame collision check UNKNOWN when machine_frame.FCStd is not provided.",
    ]

    return {
        "run_dir": str(artifacts.run_dir),
        "has_canonical_part": artifacts.canonical_part is not None,
        "has_search_result": artifacts.search_result is not None,
        "has_verification_result": artifacts.verification_result is not None,
        "has_geometry_brep": has_geom,
        "has_flat_pattern_brep": has_flat,
        "step_count": len(candidate_seq),
        "bend_ids": bend_ids,
        "limitations": limitations,
    }


def present_verification(artifacts: RunArtifacts) -> Dict[str, Any]:
    """
    Format verification status and eligibility per Q5 / README color contract.
    - VERIFIED: production_eligible=true -> green
    - VERIFICATION_FAILED: production_eligible=false -> orange
    - UNVERIFIED: missing 09_verification_result.json -> gray
    """
    if artifacts.verification_result is None:
        status = BADGE_UNVERIFIED
        eligible: Optional[bool] = None
        reasons: List[str] = ["Файл 09_verification_result.json отсутствует в директории run."]
        summary = (
            "Статус: UNVERIFIED\n"
            "Прогон не верифицирован (файл 09 отсутствует). "
            "Последовательность отображается только в ознакомительном режиме."
        )
    else:
        payload = artifacts.verification_result.get("payload", {})
        raw_status = payload.get("verification_status")
        raw_eligible = payload.get("production_eligible")

        # KL-26: Fail-fast on inconsistent verification payload (Invariant #1, #2)
        if raw_status == "VERIFIED" and raw_eligible is False:
            raise ValueError(
                "Inconsistent 09_verification_result: verification_status='VERIFIED' "
                "but production_eligible=False. Honest invariant violation (KL-26)."
            )

        if raw_eligible is True:
            status = BADGE_VERIFIED
            eligible = True
            reasons = []
            summary = (
                "Статус: VERIFIED\n"
                "Последовательность успешно проверена солвером верификации. "
                "Пригодна к производству (production_eligible=true)."
            )
        else:
            status = BADGE_VERIFICATION_FAILED
            eligible = False
            reasons = payload.get("reasons", ["Отклонено верификатором (столкновение или нарушение ограничений)"])
            summary = (
                f"Статус: VERIFICATION_FAILED\n"
                f"Последовательность отклонена верификатором (production_eligible=false).\n"
                f"Причины: {', '.join(str(r) for r in reasons)}"
            )

    return {
        "status": status,
        "production_eligible": eligible,
        "color_rgba": get_badge_rgba(status),
        "color_hex": get_badge_hex(status),
        "reasons": reasons,
        "summary": summary,
    }


def get_player(artifacts: RunArtifacts) -> Any:
    """Build Player for simulation playback (F.2 + F.3a).

    Lazy imports of OCC backend keep bridge headless-safe for plain python3 tests.
    Eagerly builds frames -> raises PlayerError / GraphBuilderError on bad 09 or run.
    Read-only: does not modify artifacts or smbs_core.
    """
    from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
    from smbs_core.viewer.graph_builder import build_graphs
    from smbs_core.viewer.player import Player

    graphs = build_graphs(artifacts)
    backend = PythonOCCBackend()
    player = Player(artifacts, graphs, backend)
    _ = player.frames  # eager build; raises PlayerError on malformed 09
    return player


def get_tooling_library(library_path: Union[str, Path, None] = None) -> Any:
    """Load ToolingLibrary from canonical repo toollibrary/ (S.71).

    Lazy import keeps bridge headless-safe for plain python3 tests.
    Raises ToolingLibraryError on missing subdirs / malformed sidecars.
    """
    from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary

    if library_path is None:
        library_path = Path(__file__).resolve().parents[2] / "toollibrary"
    return ToolingLibrary(Path(library_path))


@dataclass
class PipelineRunResult:
    """Structured result of executing the SMBS planning pipeline on an FCStd model."""
    run_dir: Path
    planning_status: str  # "SUCCESS", "NO_SOLUTION", "ERROR", "TIMEOUT", "PARTIAL"
    candidate_sequence: List[Dict[str, Any]] = field(default_factory=list)
    part_id: Optional[str] = None
    verification_status: Optional[str] = None
    production_eligible: Optional[bool] = None
    error_message: Optional[str] = None
    diagnostics: Any = None
    artifacts: Optional[RunArtifacts] = None
    status: Optional[str] = None


def run_pipeline_on_fcstd(
    fcstd_path: Union[str, Path],
    *,
    library_path: Union[str, Path, None] = None,
    punch_id: Optional[str] = None,
    die_id: Optional[str] = None,
    tooling_set_id: Optional[str] = None,
    clamps_config: Optional[Any] = None,
    search_algorithm: str = "astar",
    budget: int = 5000,
    timeout: float = 60.0,
    out_runs_dir: Union[str, Path, None] = None,
    k_factor_override: Optional[float] = None,
    unknown_policy: str = "PRODUCTION",
    coordinator_cls: Optional[Any] = None,
) -> PipelineRunResult:
    """
    Programmatic entrypoint to run the full SMBS pipeline on an FCStd file.
    Reuses smbs_core.coordinator.run_pipeline.Coordinator directly.
    """
    path = Path(fcstd_path).expanduser().resolve()
    if not path.is_file():
        return PipelineRunResult(
            run_dir=Path(),
            planning_status="ERROR",
            candidate_sequence=[],
            part_id=path.stem,
            error_message=f"Файл детали не найден: {path}",
            diagnostics=[f"File not found: {path}"],
        )

    # Determine workspace_root
    repo_root = Path(__file__).resolve().parents[2]
    if out_runs_dir is not None:
        out_path = Path(out_runs_dir).expanduser().resolve()
        if out_path.name == "runs":
            workspace_root = out_path.parent
        else:
            workspace_root = out_path
    else:
        workspace_root = repo_root

    # Resolve tooling
    default_lib = repo_root / "toollibrary"
    lib_path = Path(library_path) if library_path else default_lib

    machine_config: Optional[Dict[str, Any]] = None
    if tooling_set_id is not None:
        machine_config = {
            "tooling_set": tooling_set_id,
            "library_path": str(lib_path),
        }
    elif punch_id is not None and die_id is not None:
        machine_config = {
            "punch_id": punch_id,
            "die_id": die_id,
            "library_path": str(lib_path),
        }

    if clamps_config is not None:
        if machine_config is None:
            machine_config = {}
        machine_config["clamps_config"] = clamps_config

    # Instantiate Coordinator (allowing injection for unit tests)
    if coordinator_cls is None:
        from smbs_core.coordinator.run_pipeline import Coordinator
        coord = Coordinator(workspace_root=workspace_root)
    else:
        coord = coordinator_cls(workspace_root=workspace_root)

    resource_budget = {
        "max_nodes": budget,
        "max_wall_time_s": timeout,
        "max_cache_entries": 10000,
        "max_geometry_ops": 20000,
        "max_trajectory_samples": 500,
        "max_memory_mb": 2048.0,
    }

    try:
        res = coord.start_run(
            source_file_path=path,
            part_id=path.stem,
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=k_factor_override,
            search_algorithm=search_algorithm,
            machine_config=machine_config,
            resource_budget=resource_budget,
            unknown_policy=unknown_policy,
        )
    except Exception as e:
        return PipelineRunResult(
            run_dir=Path(),
            planning_status="ERROR",
            candidate_sequence=[],
            part_id=path.stem,
            error_message=f"Ошибка выполнения конвейера: {e}",
            diagnostics=[str(e)],
        )

    run_dir = Path(res.get("run_dir", ""))
    planning_status = res.get("planning_status", "UNKNOWN")
    sequence = res.get("sequence", [])
    verif_status = res.get("verification_status")
    prod_elig = res.get("production_eligible")
    status = res.get("status")
    failed_stage = res.get("stage")

    # Extract diagnostics and error messages
    diagnostics = res.get("run_report", {}).get("diagnostics", [])
    if not diagnostics and "diagnostics" in res:
        diagnostics = res.get("diagnostics", [])

    if not diagnostics and "search_result_path" in res:
        try:
            import json
            sr_file = Path(res["search_result_path"])
            if sr_file.is_file():
                sr_data = json.loads(sr_file.read_text(encoding="utf-8"))
                diagnostics = sr_data.get("diagnostics", [])
        except Exception:
            pass

    error_message: Optional[str] = None
    if planning_status != "SUCCESS":
        if diagnostics:
            # Format diagnostics clearly with stage and message
            diag_strs = []
            for d in diagnostics:
                if isinstance(d, dict):
                    stage_pfx = f"[{d.get('stage')}] " if d.get('stage') else ""
                    code_s = f"({d.get('code')}) " if d.get('code') else ""
                    diag_strs.append(f"{stage_pfx}{code_s}{d.get('message', str(d))}")
                else:
                    diag_strs.append(str(d))
            error_message = "; ".join(diag_strs)
        elif failed_stage:
            error_message = f"Отказ на этапе конвейера: {failed_stage} (статус: {status})"
        else:
            error_message = f"Последовательность не найдена ({planning_status})"

    # Try loading RunArtifacts
    artifacts: Optional[RunArtifacts] = None
    if run_dir.is_dir():
        try:
            artifacts = load_run(run_dir)
        except Exception:
            pass

    return PipelineRunResult(
        run_dir=run_dir,
        planning_status=planning_status,
        candidate_sequence=sequence,
        part_id=path.stem,
        verification_status=verif_status,
        production_eligible=prod_elig,
        error_message=error_message,
        diagnostics=diagnostics,
        artifacts=artifacts,
        status=status,
    )

