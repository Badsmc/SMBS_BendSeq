"""Canonical repository-local demo FreeCAD fixture location.

Only demo parts belong here; production machine/tooling models remain in
'toollibrary' so existing tooling selection and library paths stay valid.
No silent fallback to developer-specific external machine paths.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT / "demo"
DEMO_PARTS = DEMO_ROOT / "parts"

REQUIRED_DEMOS = ("L-(ok).FCStd", "Z-(ok).FCStd", "135-(ok).FCStd")


def find_demo_part(filename: str) -> Path:
    """Resolve a demo FCStd by basename within the checked-out repository."""
    name = Path(filename)
    if (name.name != filename or filename in (".", "..") or "/" in filename
            or "\\" in filename or not filename.lower().endswith(".fcstd")):
        raise ValueError(f"Invalid demo filename: {filename!r}")
    # Canonical location first, then optional flat demo layout for convenience.
    candidates = (DEMO_PARTS / filename, DEMO_ROOT / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Demo FCStd '{filename}' is missing from this installation. "
        f"Expected: {searched}. "
        "Copy the actual source fixture into demo/parts and commit its binary file; "
        "the repository does not automatically generate reference CAD geometry."
    )


# The 2D preview and the D6 native 3D runtime use one repository-local
# demo directory. Keep both historical APIs for compatibility.
REPO_ROOT = PROJECT_ROOT
DEMO_DIR = DEMO_ROOT
DEMO_PARTS_DIR = DEMO_PARTS
REFERENCE_PARTS = REQUIRED_DEMOS


def demo_part_path(filename: str, *, required: bool = True) -> Path:
    """2D API alias resolving only the canonical demo/parts directory."""
    name = Path(filename)
    if not isinstance(filename, str) or not filename or name.name != filename or (
        filename in (".", "..") or "/" in filename or "\\" in filename
        or not filename.lower().endswith(".fcstd")
    ):
        raise ValueError(f"Unsafe demo FCStd filename: {filename!r}")
    path = DEMO_PARTS / filename
    if required and not path.is_file():
        raise FileNotFoundError(
            f"Missing demo FreeCAD model: {path}. Add the original file to demo/parts/."
        )
    return path


def all_demo_parts() -> tuple[Path, ...]:
    """Return all available original demo FCStd files in deterministic order."""
    if not DEMO_PARTS.is_dir():
        return ()
    return tuple(sorted(
        (p for p in DEMO_PARTS.iterdir()
         if p.is_file() and p.suffix.lower() == ".fcstd"),
        key=lambda p: p.name.casefold(),
    ))
