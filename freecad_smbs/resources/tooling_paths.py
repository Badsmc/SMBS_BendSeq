"""
tooling_paths.py - Canonical tooling file paths and missing tooling registry (β1).

References toollibrary/ directory. Does NOT create or synthesize missing .FCStd files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

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

# Registry of missing tools per β1.
# NOTE: computed from canonical Path.exists() at import time. OS-dependent for
# paths with case/naming variants (e.g. Backgauge/ vs backgauge/, Clamp/ vs
# clamps/). For accurate presence checks use resolve_backgauge_fcstd(),
# resolve_clamp_fcstd(), and resolve_machine_frame_fcstd().
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


# ---------------------------------------------------------------------------
# Case-insensitive file resolution (S.71 C2b)
#
# Canonical paths assume lowercase + singular naming. Real user files may use
# mixed case ("Backgauge/Backgauge.FCStd", "Clamp/Clamp.FCStd") or alternate
# filenames ("clamps.FCStd" vs "Clamp.FCStd"). These resolvers find the actual
# .FCStd file with case/naming fallback, OS-independent (APFS, ext4, NTFS).
# ---------------------------------------------------------------------------

def _find_fcstd_case_insensitive(directory: Path) -> Optional[Path]:
    """Return first *.FCStd (case-insensitive ext) in directory, or None."""
    if not directory.exists() or not directory.is_dir():
        return None
    for f in directory.iterdir():
        if f.is_file() and f.suffix.lower() == ".fcstd":
            return f
    return None


def resolve_backgauge_fcstd(library_root: Path) -> Optional[Path]:
    """Resolve backgauge .FCStd with case-insensitive fallback.

    Canonical: library_root/backgauge/backgauge.FCStd
    Fallback: any dir case-variant (Backgauge/, BACKGAUGE/) → first *.FCStd
    """
    if not library_root.exists() or not library_root.is_dir():
        return None
    # 1. Exact canonical check: if both directory and file exist with exact casing
    for d in library_root.iterdir():
        if d.is_dir() and d.name == "backgauge":
            for f in d.iterdir():
                if f.is_file() and f.name == "backgauge.FCStd":
                    return f
            return _find_fcstd_case_insensitive(d)
    # 2. Case-insensitive fallback
    for d in library_root.iterdir():
        if d.is_dir() and d.name.lower() == "backgauge":
            return _find_fcstd_case_insensitive(d)
    return None


def resolve_clamp_fcstd(library_root: Path) -> Optional[Path]:
    """Resolve clamp .FCStd with case + singular/plural fallback.

    Canonical candidates (in order):
      library_root/clamps/clamps.FCStd   (plural, canonical)
      library_root/clamps/clamp.FCStd    (plural dir, singular file)
      library_root/clamp/clamp.FCStd     (singular dir, singular file)
    Fallback: any dir named Clamp(s)/clamp(s) → first *.FCStd
    """
    if not library_root.exists() or not library_root.is_dir():
        return None
    # 1. Exact canonical candidates
    for dir_name, file_name in (
        ("clamps", "clamps.FCStd"),
        ("clamps", "clamp.FCStd"),
        ("clamp", "clamp.FCStd"),
    ):
        for d in library_root.iterdir():
            if d.is_dir() and d.name == dir_name:
                for f in d.iterdir():
                    if f.is_file() and f.name == file_name:
                        return f
    # 2. Case/naming-insensitive fallback
    for d in library_root.iterdir():
        if d.is_dir() and d.name.lower() in ("clamps", "clamp"):
            found = _find_fcstd_case_insensitive(d)
            if found:
                return found
    return None


def resolve_machine_frame_fcstd(library_root: Path) -> Optional[Path]:
    """Resolve machine_frame .FCStd with directory and naming fallback (KL-16).

    Canonical candidates (in order):
      library_root/machine_frame/machine_frame.FCStd (canonical KL-16)
      library_root/machine_frame.FCStd               (root legacy)
      library_root/presses/machine_frame.FCStd       (presses dir)
      library_root/presses/cncbender.FCStd           (presses fallback)
    Fallback: any dir named machine_frame/frame/presses -> first *.FCStd
    """
    if not library_root.exists() or not library_root.is_dir():
        return None
    # 1. Exact canonical candidates
    for dir_name, file_name in (
        ("machine_frame", "machine_frame.FCStd"),
        ("", "machine_frame.FCStd"),
        ("presses", "machine_frame.FCStd"),
        ("presses", "cncbender.FCStd"),
    ):
        target = (library_root / dir_name / file_name) if dir_name else (library_root / file_name)
        if target.is_file():
            return target

    # 2. Case/naming-insensitive directory search
    for d in library_root.iterdir():
        if d.is_dir() and d.name.lower() in ("machine_frame", "machineframe", "frame", "presses"):
            found = _find_fcstd_case_insensitive(d)
            if found:
                return found
    return None
