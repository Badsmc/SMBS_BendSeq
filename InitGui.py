"""FreeCAD root-level GUI entry point when the whole repository is in Mod/.

The workbench, menus and commands are implemented only in freecad_smbs/InitGui.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.InitGui import *  # noqa: F401,F403
