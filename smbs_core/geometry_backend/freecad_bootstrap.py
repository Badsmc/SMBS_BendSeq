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

    # Discover versioned installations on the current host only. Explicit
    # overrides also cover portable installations and extracted AppImages.
    if sys.platform == "darwin":
        for base in (Path("/Applications"), Path.home() / "Applications"):
            for bundle in sorted(base.glob("FreeCAD*.app"), reverse=True):
                candidates.extend(str(bundle / suffix) for suffix in (
                    "Contents/Resources/lib", "Contents/lib"))
    elif os.name == "nt":
        bases = [os.environ.get(name) for name in
                 ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
        bases.append(str(Path.home() / "AppData" / "Local" / "Programs"))
        for base in filter(None, bases):
            for install in sorted(Path(base).glob("FreeCAD*"), reverse=True):
                candidates.extend(str(install / sub) for sub in ("bin", "lib"))
    else:
        candidates.extend([
            "/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib",
            "/usr/local/lib/freecad/lib", "/usr/lib/freecad-daily/lib",
        ])
        appdir = os.environ.get("APPDIR")
        if appdir:
            candidates.extend(str(Path(appdir) / sub) for sub in
                              ("usr/lib", "usr/lib/freecad/lib"))
    # Conda environment prefix
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.extend([
            os.path.join(conda_prefix, "lib"),
            os.path.join(conda_prefix, "Library", "bin"),
        ])
    return list(dict.fromkeys(os.path.expandvars(os.path.expanduser(p))
                              for p in candidates))


def get_sheetmetal_mod_candidates() -> List[str]:
    """Returns prioritized candidate paths for SheetMetal workbench addon directory."""
    candidates: List[str] = []
    # 1. Explicit environment variable override
    env_mod = os.environ.get("FREECAD_MOD_SHEETMETAL")
    if env_mod:
        candidates.append(env_mod)

    # If running in FreeCAD GUI, use its authoritative configuration path.
    # This covers portable installations, snap, Flatpak and custom layouts.
    freecad_runtime = sys.modules.get("FreeCAD")
    if freecad_runtime is not None and hasattr(freecad_runtime, "getUserAppDataDir"):
        try:
            runtime_root = Path(freecad_runtime.getUserAppDataDir())
            for name in ("sheetmetal", "SheetMetal"):
                candidates.append(str(runtime_root / "Mod" / name))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    # 2. Standard user addon locations. FreeCAD 1.1 keeps user addons
    # in versioned Mod/ directories on Windows, macOS and Linux.
    home = Path.home()
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    xdg_data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    version_dirs = ("v1-1", "v1-0", "")
    for base in (
        *(Path(p) / "FreeCAD" for p in (appdata, localappdata) if p),
        home / "Library" / "Application Support" / "FreeCAD",
        xdg_data / "FreeCAD",
        home / ".FreeCAD",
        home / "snap" / "freecad" / "common",
        home / ".var" / "app" / "org.freecad.FreeCAD" / "data" / "FreeCAD",
    ):
        discovered = sorted((p.name for p in base.glob("v*")), reverse=True)
        for version in dict.fromkeys([*discovered, *version_dirs]):
            candidates.extend(
                str(base / version / "Mod" / name)
                for name in ("sheetmetal", "SheetMetal")
            )
    candidates.extend([
        "/usr/share/freecad/Mod/sheetmetal",
        "/usr/share/freecad/Mod/SheetMetal",
    ])
    return list(dict.fromkeys(os.path.expandvars(os.path.expanduser(p))
                              for p in candidates))


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
