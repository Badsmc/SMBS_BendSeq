"""Cross-platform paths to version-controlled FreeCAD demo workpieces.

The repository root is determined from this module's location rather than
user home, OS, FreeCAD installation path or the current working directory.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demo"
DEMO_PARTS_DIR = DEMO_DIR / "parts"
REFERENCE_PARTS = (
    "L-(ok).FCStd",
    "Z-(ok).FCStd",
    "135-(ok).FCStd",
)


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
