"""Cross-platform paths to version-controlled FreeCAD demo workpieces.

The repository root is determined from this module's location rather than
user home, OS, FreeCAD installation path or the current working directory.

Note on 17-model historical archive:
The 16 active demo models in demo/parts were imported from AutoMod.
The fixture 'box.100out-50in-(ok).FCStd' was replaced from BendSeq_Fixtures_Source
(size: 86,662 bytes, SHA-256: 6510d62cdf6427cfac18d936243c39f8aafa4e31718248124105642ad9e52db3)
to supply the mandatory SheetMetal Body_Unfold object.
The separate 17-model historical archive from SMBS_demo_FCStd_17_files.zip is NOT
restored and is not declared restored.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demo"
DEMO_PARTS_DIR = DEMO_DIR / "parts"
MANIFEST_PATH = DEMO_DIR / "MANIFEST.json"

REFERENCE_PARTS = (
    "L-(ok).FCStd",
    "Z-(ok).FCStd",
    "135-(ok).FCStd",
)

# Active fixture provenance & hash registry
BOX_100OUT_REPLACED_SHA256 = "6510d62cdf6427cfac18d936243c39f8aafa4e31718248124105642ad9e52db3"
BOX_100OUT_REPLACED_SIZE = 86662
BOX_100OUT_SOURCE = "BendSeq_Fixtures_Source/box.100out-50in-(ok).FCStd"


def demo_part_path(filename: str, *, required: bool = True) -> Path:
    """Resolve a workpiece within demo/parts, without searching outside the repo."""
    if not isinstance(filename, str) or not filename:
        raise ValueError("Demo filename must be non-empty")
    # Explicitly reject both POSIX and Windows separators on every platform.
    if (
        filename in (".", "..")
        or "/" in filename
        or "\\" in filename
        or Path(filename).name != filename
        or not filename.lower().endswith(".fcstd")
    ):
        raise ValueError(f"Unsafe demo FCStd filename: {filename!r}")
    result = DEMO_PARTS_DIR / filename
    if required and not result.is_file():
        raise FileNotFoundError(
            f"Missing demo FreeCAD model: {result}. "
            "Add the original .FCStd file to the repository's demo/parts/ "
            "directory (do not substitute generated fixture geometry)."
        )
    return result


def all_demo_parts() -> tuple[Path, ...]:
    """Return present models in a stable order without modifying disk."""
    if not DEMO_PARTS_DIR.is_dir():
        return ()
    return tuple(sorted(
        (p for p in DEMO_PARTS_DIR.iterdir()
         if p.is_file() and p.suffix.lower() == ".fcstd"),
        key=lambda p: p.name.casefold(),
    ))


def get_demo_part_sha256(filename: str) -> str:
    """Compute exact SHA-256 hash of a demo fixture."""
    p = demo_part_path(filename, required=True)
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_manifest() -> Dict[str, Any]:
    """Load demo/MANIFEST.json."""
    if not MANIFEST_PATH.is_file():
        return {}
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
