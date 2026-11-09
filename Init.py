"""FreeCAD Mod entry point; FreeCAD can execute this without __file__.

Resolve the addon only when Python has not already made it importable. Reject
ambiguous installations rather than silently loading an unrelated checkout.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _ensure_addon_on_path():
    if importlib.util.find_spec("freecad_smbs") is not None:
        return

    candidates = []
    source = globals().get("__file__")
    if source:
        candidates.append(Path(source).resolve().parent)
    try:
        import FreeCAD as App
        mod_home = Path(App.getUserAppDataDir()) / "Mod"
        if mod_home.is_dir():
            candidates.extend(
                item for item in mod_home.iterdir()
                if item.is_dir() and item.name.lower().startswith("smbs")
            )
    except (ImportError, AttributeError, OSError):
        pass

    candidates = sorted({
        str(item.resolve()) for item in candidates
        if (item / "freecad_smbs" / "__init__.py").is_file()
    })
    if len(candidates) != 1:
        raise RuntimeError(
            "Cannot resolve unique SMBS installation: {}. Keep one SMBS "
            "checkout inside FreeCAD Mod and restart FreeCAD.".format(candidates)
        )
    sys.path.insert(0, candidates[0])


_ensure_addon_on_path()

from freecad_smbs.Init import *  # noqa: F401,F403
