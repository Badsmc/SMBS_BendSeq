"""
smbs_bridge.py - Read-only adapter bridge between freecad_smbs and smbs_core.

Enforces strict read-only access to run artifacts without mutating smbs_core/.
"""
from __future__ import annotations

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
        "KL-12: panel shells disconnect after first unfold (KL-12 v3 scope, PREVIEW_ONLY).",
        "KL-16: machine frame pending supply of machine_frame.FCStd.",
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
        raw_eligible = payload.get("production_eligible")
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

