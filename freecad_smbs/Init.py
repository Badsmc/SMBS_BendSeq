"""
Init.py - Headless initialization for FreeCAD SMBS Workbench.

Safe to import in console / headless environments without Qt or FreeCADGui.
No top-level GUI imports are allowed in this file.
"""
from __future__ import annotations

import sys

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
