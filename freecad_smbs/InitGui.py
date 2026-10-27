"""
InitGui.py - GUI initialization for FreeCAD SMBS Workbench.

Registers workbench, commands, menus, and toolbars in FreeCAD GUI.
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

import sys
from pathlib import Path

try:
    if "__file__" in globals():
        _repo_root = str(Path(__file__).resolve().parent.parent)
    else:
        import freecad_smbs
        _repo_root = str(Path(freecad_smbs.__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
except Exception:
    pass

# Import command classes (duck-typed, headless-safe)
from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.open_run import OpenRunCommand
from freecad_smbs.commands.simulate import SimulateCommand
from freecad_smbs.commands.tooling import ToolingCommand
from freecad_smbs.commands.verify import VerifyCommand

# Command registry identifiers
CMD_OPEN_RUN: str = "SMBS_OpenRun"
CMD_CALCULATE: str = "SMBS_Calculate"
CMD_SIMULATE: str = "SMBS_Simulate"
CMD_TOOLING: str = "SMBS_Tooling"
CMD_VERIFY: str = "SMBS_Verify"
CMD_INFO: str = "SMBS_Info"

# Command definitions mapping
COMMANDS_MAP = {
    CMD_OPEN_RUN: OpenRunCommand,
    CMD_CALCULATE: CalculateCommand,
    CMD_SIMULATE: SimulateCommand,
    CMD_TOOLING: ToolingCommand,
    CMD_VERIFY: VerifyCommand,
    CMD_INFO: InfoCommand,
}


def register_commands() -> None:
    """Register SMBS commands in FreeCADGui command registry."""
    if FreeCADGui is None or not hasattr(FreeCADGui, "addCommand"):
        return

    for cmd_name, cmd_cls in COMMANDS_MAP.items():
        try:
            FreeCADGui.addCommand(cmd_name, cmd_cls())
        except Exception:
            # FreeCAD may re-init workbench; addCommand is idempotent; swallow for safety
            pass


class SMBSWorkbench(Workbench):  # type: ignore[misc]
    """FreeCAD GUI Workbench for SMBS."""

    MenuText = "SMBS"
    ToolTip = "Sheet Metal Bending Sequence Planner"
    Icon = ""

    def Initialize(self) -> None:
        """Initialize workbench UI components (commands, menus, toolbars)."""
        # C2-2: Register commands BEFORE appendMenu / appendToolbar
        register_commands()

        # Menu layout per Spec-final §3.2
        self.menu_items: List[str] = [
            CMD_OPEN_RUN,
            "Separator",
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            "Separator",
            CMD_VERIFY,
            CMD_INFO,
        ]
        if hasattr(self, "appendMenu"):
            self.appendMenu(["SMBS"], self.menu_items)

        # Toolbar layout per Spec-final §3.2
        self.toolbar_items: List[str] = [
            CMD_OPEN_RUN,
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            CMD_VERIFY,
            CMD_INFO,
        ]
        if hasattr(self, "appendToolbar"):
            self.appendToolbar("SMBS", self.toolbar_items)

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
