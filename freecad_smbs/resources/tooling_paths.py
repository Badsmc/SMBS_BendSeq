"""
tooling_paths.py - Canonical tooling file paths and missing tooling registry (β1).

References toollibrary/ directory. Does NOT create or synthesize missing .FCStd files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# Repository and Tooling Library root resolution
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
TOOL_LIBRARY_ROOT: Path = REPO_ROOT / "toollibrary"

# Canonical paths for existing tooling assets
PUNCH_PATH: Path = TOOL_LIBRARY_ROOT / "punch" / "puancon.FCStd"
DIE_PATH: Path = TOOL_LIBRARY_ROOT / "die" / "matrica.FCStd"
PRESS_PATH: Path = TOOL_LIBRARY_ROOT / "presses" / "cncbender.FCStd"

# Missing tooling paths (per β1 — expected path conventions, missing until client provides)
BACKGAUGE_PATH: Path = TOOL_LIBRARY_ROOT / "backgauge" / "backgauge.FCStd"
CLAMPS_PATH: Path = TOOL_LIBRARY_ROOT / "clamps" / "clamps.FCStd"
MACHINE_FRAME_PATH: Path = TOOL_LIBRARY_ROOT / "machine_frame" / "machine_frame.FCStd"

# Canonical mapping of all tooling entities
ALL_TOOLING_PATHS: Dict[str, Path] = {
    "punch": PUNCH_PATH,
    "die": DIE_PATH,
    "press": PRESS_PATH,
    "backgauge": BACKGAUGE_PATH,
    "clamps": CLAMPS_PATH,
    "machine_frame": MACHINE_FRAME_PATH,
}

# Registry of missing tools per β1
MISSING_TOOLS: List[str] = [
    name for name, path in ALL_TOOLING_PATHS.items() if not path.exists()
]


def is_tool_available(name: str) -> bool:
    """Return True if the specified tooling file exists in toollibrary/."""
    path = ALL_TOOLING_PATHS.get(name)
    return path is not None and path.exists()


def get_tool_path_or_missing(name: str) -> Path:
    """Return tooling Path regardless of existence for status inspection."""
    if name not in ALL_TOOLING_PATHS:
        raise KeyError(f"Unknown tooling entity '{name}'")
    return ALL_TOOLING_PATHS[name]
