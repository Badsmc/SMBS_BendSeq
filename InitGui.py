"""FreeCAD direct-Mod GUI entry point.

FreeCAD executes this file with exec(compile(...)) and does not inject
__file__; resolve the checkout from its registered module search path.
Only the canonical freecad_smbs.InitGui registers the Workbench.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _checkout_root() -> Path:
    # Normal Python execution (tests or manual imports).
    if "__file__" in globals():
        return Path(__file__).resolve().parent
    # FreeCAD DirModGui uses exec(code), which does not define __file__.
    # The FreeCAD loader adds Mod search directories to sys.path.
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry).resolve()
        if (candidate / "freecad_smbs" / "InitGui.py").is_file():
            return candidate
    # Some FreeCAD installations register Mod objects without adding the
    # individual checkout to sys.path before executing InitGui.py.
    try:
        import FreeCAD as App
        for mod in getattr(App, "__ModCache__", ()):
            candidate = Path(mod.path).resolve()
            if (candidate / "freecad_smbs" / "InitGui.py").is_file():
                return candidate
    except (ImportError, AttributeError, TypeError, OSError):
        pass
    raise ImportError(
        "PressBrakeSim checkout not found in sys.path; install the entire "
        "2D checkout under the active FreeCAD Mod directory."
    )


_REPO_ROOT = _checkout_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.InitGui import *  # noqa: F401,F403
