"""
freecad_bootstrap.py - Centralized runtime environment bootstrapper for FreeCAD and SheetMetal.

Dynamically locates and injects FreeCAD shared libraries and SheetMetal workbench modules
into sys.path across platforms (macOS, Linux, Windows, Conda, CI) before importing FreeCAD/Part.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List


def get_freecad_lib_candidates() -> List[str]:
    """Returns prioritized candidate paths for FreeCAD C++/Python library directory."""
    candidates: List[str] = []
    # 1. Explicit environment variable override
    env_lib = os.environ.get("FREECAD_LIB")
    if env_lib:
        candidates.append(env_lib)

    # 2. Platform-specific standard installation directories
    # macOS application bundle
    candidates.extend([
        "/Applications/FreeCAD.app/Contents/Resources/lib",
        "/Applications/FreeCAD 1.1.app/Contents/Resources/lib",
        "/Applications/FreeCAD 1.0.app/Contents/Resources/lib",
    ])
    # Linux distribution packages / containers
    candidates.extend([
        "/usr/lib/freecad/lib",
        "/usr/lib/freecad-python3/lib",
        "/usr/local/lib/freecad/lib",
        "/usr/lib/freecad-daily/lib",
    ])
    # Windows standard paths
    candidates.extend([
        r"C:\Program Files\FreeCAD 1.1\bin",
        r"C:\Program Files\FreeCAD 1.0\bin",
        r"C:\Program Files\FreeCAD 0.21\bin",
        r"C:\Program Files\FreeCAD\bin",
    ])
    # Conda environment prefix
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.extend([
            os.path.join(conda_prefix, "lib"),
            os.path.join(conda_prefix, "Library", "bin"),
        ])
    return candidates


def get_sheetmetal_mod_candidates() -> List[str]:
    """Returns prioritized candidate paths for SheetMetal workbench addon directory."""
    candidates: List[str] = []
    # 1. Explicit environment variable override
    env_mod = os.environ.get("FREECAD_MOD_SHEETMETAL")
    if env_mod:
        candidates.append(env_mod)

    # 2. Standard user addon locations
    home = Path.home()
    candidates.extend([
        # macOS FreeCAD 1.1 / 1.0
        str(home / "Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"),
        str(home / "Library/Application Support/FreeCAD/v1-0/Mod/sheetmetal"),
        str(home / "Library/Application Support/FreeCAD/Mod/sheetmetal"),
        # Linux standard XDG data home
        str(home / ".local/share/FreeCAD/Mod/sheetmetal"),
        str(home / ".FreeCAD/Mod/sheetmetal"),
        "/usr/share/freecad/Mod/sheetmetal",
        # Windows %APPDATA%
        os.path.expandvars(r"%APPDATA%\FreeCAD\Mod\sheetmetal"),
    ])
    return candidates


def ensure_freecad_paths() -> bool:
    """
    Injects valid FreeCAD library and SheetMetal paths into sys.path.
    Returns True if a FreeCAD library path was detected on the filesystem.
    """
    found_lib = False
    for p in get_freecad_lib_candidates():
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
            found_lib = True
        elif p and os.path.isdir(p):
            found_lib = True

    for p in get_sheetmetal_mod_candidates():
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    return found_lib
