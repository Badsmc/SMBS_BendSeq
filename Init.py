"""FreeCAD root entry point, compatible with FreeCAD exec(code)."""
from __future__ import annotations

import sys
from pathlib import Path


def _checkout_root() -> Path:
    if "__file__" in globals():
        return Path(__file__).resolve().parent
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry).resolve()
        if (candidate / "freecad_smbs" / "Init.py").is_file():
            return candidate
    raise ImportError(
        "PressBrakeSim checkout not found in sys.path; install the entire "
        "2D checkout under the active FreeCAD Mod directory."
    )


_REPO_ROOT = _checkout_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.Init import *  # noqa: F401,F403
