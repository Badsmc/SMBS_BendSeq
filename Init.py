"""FreeCAD root-level entry point for a directly installed SMBS repository.

The canonical implementation lives in freecad_smbs/Init.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.Init import *  # noqa: F401,F403
