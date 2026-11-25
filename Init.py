"""FreeCAD root entry point, compatible with FreeCAD exec(code)."""
from __future__ import annotations

import sys


def _checkout_root(script_file):
    # FreeCAD can execute this script with separate globals and locals.
    import sys
    from pathlib import Path

    code_path = Path(script_file)
    if code_path.is_absolute():
        candidate = code_path.resolve().parent
        if (candidate / "freecad_smbs" / "Init.py").is_file():
            return candidate
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry).resolve()
        if (candidate / "freecad_smbs" / "Init.py").is_file():
            return candidate
    # Some FreeCAD installations register Mod objects without adding the
    # individual checkout to sys.path before executing InitGui.py.
    try:
        import FreeCAD as App
        for mod in getattr(App, "__ModCache__", ()):
            candidate = Path(mod.path).resolve()
            if (candidate / "freecad_smbs" / "Init.py").is_file():
                return candidate
    except (ImportError, AttributeError, TypeError, OSError):
        pass
    raise ImportError(
        "PressBrakeSim checkout not found in sys.path; install the entire "
        "2D checkout under the active FreeCAD Mod directory."
    )


_REPO_ROOT = _checkout_root(sys._getframe().f_code.co_filename)
# FreeCAD registers legacy addon paths. Check them without changing sys.path.
import importlib.util
from pathlib import Path
for _module_name in ("freecad_smbs", "smbs_core"):
    _spec = importlib.util.find_spec(_module_name)
    if _spec is None or not _spec.origin or Path(_spec.origin).resolve().parent.parent != _REPO_ROOT:
        raise ImportError("SMBS: missing or conflicting package " + _module_name + "; keep one installation.")

from freecad_smbs.Init import *  # noqa: F401,F403
