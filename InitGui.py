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
    # FreeCAD may execute a compiled script without setting __file__.
    # Its code object still carries the actual filename; prefer that
    # specific checkout over the first unrelated SMBS copy on sys.path.
    code_path = Path(sys._getframe(1).f_code.co_filename)
    if code_path.is_absolute():
        candidate = code_path.resolve().parent
        if (candidate / "freecad_smbs" / "InitGui.py").is_file():
            return candidate
    # Fallback for loaders that use a synthetic code filename.
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
_loaded_package = sys.modules.get("freecad_smbs")
if _loaded_package is not None:
    _loaded_file = getattr(_loaded_package, "__file__", None)
    if _loaded_file is not None:
        _loaded_root = Path(_loaded_file).resolve().parent.parent
        if _loaded_root != _REPO_ROOT:
            raise ImportError(
                "SMBS: FreeCAD already imported a different add-on copy: "
                "{}; current checkout: {}. Keep one SMBS installation and "
                "restart FreeCAD.".format(_loaded_root, _REPO_ROOT)
            )
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from freecad_smbs.InitGui import *  # noqa: F401,F403
except Exception:
    # FreeCAD normally reports only the last exception line during startup.
    # Print the complete chain (including a nested module's actual file/line)
    # to distinguish a broken import from a similarly named old SMBS checkout.
    import traceback
    message = (
        "SMBS InitGui import failed; checkout={!s}; "
        "loaded freecad_smbs={!s}\\n{}".format(
            _REPO_ROOT,
            getattr(sys.modules.get("freecad_smbs"), "__file__", "<not loaded>"),
            traceback.format_exc(),
        )
    )
    try:
        import FreeCAD as App
        App.Console.PrintError(message + "\\n")
    except (ImportError, AttributeError):
        print(message, file=sys.stderr)
    raise
