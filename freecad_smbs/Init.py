"""
Init.py - Headless initialization for FreeCAD SMBS Workbench.

Safe to import in console / headless environments without Qt or FreeCADGui.
No top-level GUI imports are allowed in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    if "__file__" in globals():
        _wb_dir = Path(__file__).resolve().parent
    else:
        import freecad_smbs
        _wb_dir = Path(freecad_smbs.__file__).resolve().parent

    _repo_root = None
    _marker = _wb_dir / ".smbs_repo_path"
    if _marker.exists():
        try:
            _p = Path(_marker.read_text(encoding="utf-8").strip())
            if _p.is_dir() and (_p / "smbs_core").is_dir():
                _repo_root = str(_p.resolve())
        except Exception:
            pass

    if not _repo_root:
        _repo_root = str(_wb_dir.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
except Exception:
    pass

try:
    import FreeCAD
except ImportError:
    FreeCAD = None


class SMBSWorkbenchHeadless:
    """Headless representation of SMBS Workbench."""

    MenuText = "SMBS"
    ToolTip = "Sheet Metal Bending Sequence Planner"
    Icon = ""

    def Initialize(self) -> None:
        """Called when workbench is initialized in console / headless mode."""
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCAD is not None and hasattr(FreeCAD, "addWorkbench"):
    try:
        FreeCAD.addWorkbench(SMBSWorkbenchHeadless)
    except Exception:
        pass
