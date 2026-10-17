"""
run_loader.py - Load run directory artifacts for viewer (Stage 4, S.11).

Scope: JSON loading + path resolution ONLY. No geometry, no OCC, no BendGraph.
Honest marking (C2): missing 09 -> verification_result is None (UNVERIFIED).
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class RunLoaderError(Exception):
    """Raised when a run directory is missing mandatory artifacts or has malformed JSON."""


@dataclass(frozen=True)
class RunArtifacts:
    run_dir: Path
    canonical_part: dict                  # from 04_canonical_part.json
    search_result: dict                   # from 07_search_result.json
    verification_result: Optional[dict]   # from 09_verification_result.json; None = UNVERIFIED
    geometry_shape_path: Path             # resolved from canonical_part.payload.geometry_artifact.file_path
    flat_pattern_path: Optional[Path]     # run_dir/flat_pattern.brep if exists, else None


def load_run(run_dir: Path) -> RunArtifacts:
    """
    Load all artifacts from a run directory.
    Raises RunLoaderError on:
      - run_dir does not exist / is not a directory
      - 04_canonical_part.json missing or malformed JSON
      - 07_search_result.json missing or malformed JSON
      - geometry_artifact.file_path missing in 04 or referenced file not found
    Does NOT raise on missing 09 (returns verification_result=None).
    Does NOT raise on missing flat_pattern.brep (returns flat_pattern_path=None).
    """
    if not isinstance(run_dir, Path):
        run_dir = Path(run_dir)

    if not run_dir.exists():
        raise RunLoaderError(f"Run directory does not exist: {run_dir}")
    if not run_dir.is_dir():
        raise RunLoaderError(f"Run directory path is not a directory: {run_dir}")

    resolved_run_dir = run_dir.resolve()

    # 1. Load 04_canonical_part.json (Mandatory)
    cp_file = resolved_run_dir / "04_canonical_part.json"
    if not cp_file.exists():
        raise RunLoaderError(f"Missing mandatory artifact: 04_canonical_part.json in {resolved_run_dir}")
    try:
        canonical_part = json.loads(cp_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RunLoaderError(f"Malformed JSON in 04_canonical_part.json: {err}") from err
    except Exception as err:
        raise RunLoaderError(f"Failed to read 04_canonical_part.json: {err}") from err

    # 2. Load 07_search_result.json (Mandatory)
    sr_file = resolved_run_dir / "07_search_result.json"
    if not sr_file.exists():
        raise RunLoaderError(f"Missing mandatory artifact: 07_search_result.json in {resolved_run_dir}")
    try:
        search_result = json.loads(sr_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RunLoaderError(f"Malformed JSON in 07_search_result.json: {err}") from err
    except Exception as err:
        raise RunLoaderError(f"Failed to read 07_search_result.json: {err}") from err

    # 3. Resolve geometry_shape_path (Mandatory)
    try:
        raw_geom_path_str = canonical_part["payload"]["geometry_artifact"]["file_path"]
    except KeyError as err:
        raise RunLoaderError(f"geometry_artifact.file_path missing in 04_canonical_part payload: {err}") from err

    raw_geom_path = Path(raw_geom_path_str)
    # Resolution priority:
    # a) run_dir / raw_geom_path.name (most direct and portable within self-contained run)
    # b) run_dir / raw_geom_path (if relative within run_dir)
    # c) raw_geom_path itself (if absolute or relative to workspace)
    geom_resolved: Optional[Path] = None
    if (resolved_run_dir / raw_geom_path.name).exists():
        geom_resolved = (resolved_run_dir / raw_geom_path.name).resolve()
    elif (resolved_run_dir / raw_geom_path).exists():
        geom_resolved = (resolved_run_dir / raw_geom_path).resolve()
    elif raw_geom_path.exists():
        geom_resolved = raw_geom_path.resolve()

    if geom_resolved is None or not geom_resolved.exists() or not geom_resolved.is_file():
        raise RunLoaderError(
            f"Referenced geometry_artifact file does not exist: '{raw_geom_path_str}' "
            f"(searched in {resolved_run_dir})"
        )

    # 4. Load 09_verification_result.json (Optional)
    # None means UNVERIFIED; do not default to production_eligible=False
    vr_file = resolved_run_dir / "09_verification_result.json"
    verification_result: Optional[dict] = None
    if vr_file.exists():
        try:
            verification_result = json.loads(vr_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise RunLoaderError(f"Malformed JSON in 09_verification_result.json: {err}") from err
        except Exception as err:
            raise RunLoaderError(f"Failed to read 09_verification_result.json: {err}") from err

    # 5. Check flat_pattern.brep (Optional)
    # Required for forward-mode in S.14; S.11 does not require it
    flat_file = resolved_run_dir / "flat_pattern.brep"
    flat_pattern_path: Optional[Path] = flat_file.resolve() if flat_file.exists() else None

    return RunArtifacts(
        run_dir=resolved_run_dir,
        canonical_part=canonical_part,
        search_result=search_result,
        verification_result=verification_result,
        geometry_shape_path=geom_resolved,
        flat_pattern_path=flat_pattern_path
    )
