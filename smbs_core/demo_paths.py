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
    if name.name != filename or filename in (".", "..") or not filename.lower().endswith(".fcstd"):
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
