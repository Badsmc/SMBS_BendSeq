"""
InitGui.py - GUI initialization for FreeCAD SMBS Workbench.

Registers workbench, menus, and toolbars in FreeCAD GUI.
"""
from __future__ import annotations

from typing import List

try:
    import FreeCAD
    import FreeCADGui
    from FreeCADGui import Workbench
except ImportError:
    FreeCAD = None
    FreeCADGui = None
    Workbench = object  # type: ignore[assignment,misc]


class SMBSWorkbench(Workbench):  # type: ignore[misc]
    """FreeCAD GUI Workbench for SMBS."""

    MenuText = "SMBS"
    ToolTip = "Sheet Metal Bending Sequence Planner"
    Icon = ""

    def Initialize(self) -> None:
        """Initialize workbench UI components (commands, menus, toolbars)."""
        self.command_list: List[str] = []
        if hasattr(self, "appendMenu"):
            self.appendMenu(["SMBS"], self.command_list)
        if hasattr(self, "appendToolbar"):
            self.appendToolbar("SMBS", self.command_list)

    def Activated(self) -> None:
        """Called when SMBS workbench is activated."""
        pass

    def Deactivated(self) -> None:
        """Called when SMBS workbench is deactivated."""
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench"):
    try:
        FreeCADGui.addWorkbench(SMBSWorkbench())
    except Exception:
        pass
