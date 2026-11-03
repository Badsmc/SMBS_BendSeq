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
    # Dynamic repo root resolution fallback in case __file__ is absent
    pass

# Import command classes (duck-typed, headless-safe)
from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.diagnostics import DiagnosticsCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.open_run import OpenRunCommand
from freecad_smbs.commands.preferences import PreferencesCommand
from freecad_smbs.commands.run_active import RunActiveCommand
from freecad_smbs.commands.simulate import SimulateCommand
from freecad_smbs.commands.tooling import ToolingCommand
from freecad_smbs.commands.verify import VerifyCommand
from freecad_smbs.commands.pressbrake_2d import PressBrake2DCommand

# Command registry identifiers
CMD_RUN_ACTIVE: str = "SMBS_RunActive"
CMD_OPEN_RUN: str = "SMBS_OpenRun"
CMD_CALCULATE: str = "SMBS_Calculate"
CMD_SIMULATE: str = "SMBS_Simulate"
CMD_TOOLING: str = "SMBS_Tooling"
CMD_VERIFY: str = "SMBS_Verify"
CMD_INFO: str = "SMBS_Info"
CMD_DIAGNOSTICS: str = "SMBS_Diagnostics"
CMD_PREFERENCES: str = "SMBS_Preferences"
CMD_PRESSBRAKE_2D: str = "SMBS_PressBrake2D"

# Command definitions mapping
COMMANDS_MAP = {
    CMD_RUN_ACTIVE: RunActiveCommand,
    CMD_OPEN_RUN: OpenRunCommand,
    CMD_CALCULATE: CalculateCommand,
    CMD_SIMULATE: SimulateCommand,
    CMD_TOOLING: ToolingCommand,
    CMD_VERIFY: VerifyCommand,
    CMD_INFO: InfoCommand,
    CMD_DIAGNOSTICS: DiagnosticsCommand,
    CMD_PREFERENCES: PreferencesCommand,
    CMD_PRESSBRAKE_2D: PressBrake2DCommand,
}


_REGISTERED_COMMANDS: set[str] = set()
_WORKBENCH_REGISTERED: bool = False


def register_commands() -> None:
    """Register SMBS commands in FreeCADGui command registry without duplication."""
    if FreeCADGui is None or not hasattr(FreeCADGui, "addCommand"):
        return

    existing: set[str] = set()
    if hasattr(FreeCADGui, "listCommands"):
        try:
            existing = set(FreeCADGui.listCommands())
        except Exception:
            existing = set()

    for cmd_name, cmd_cls in COMMANDS_MAP.items():
        if cmd_name in _REGISTERED_COMMANDS or cmd_name in existing:
            _REGISTERED_COMMANDS.add(cmd_name)
            continue
        try:
            FreeCADGui.addCommand(cmd_name, cmd_cls())
            _REGISTERED_COMMANDS.add(cmd_name)
        except Exception:
            pass


class SMBSWorkbench(Workbench):  # type: ignore[misc]
    """FreeCAD GUI Workbench for SMBS."""

    MenuText = "PressBrakeSim 2D"
    ToolTip = "PressBrakeSim 2D interface preview (development branch)"
    Icon = ""

    CMD_RUN_ACTIVE = CMD_RUN_ACTIVE
    CMD_OPEN_RUN = CMD_OPEN_RUN
    CMD_CALCULATE = CMD_CALCULATE
    CMD_SIMULATE = CMD_SIMULATE
    CMD_TOOLING = CMD_TOOLING
    CMD_VERIFY = CMD_VERIFY
    CMD_INFO = CMD_INFO
    CMD_DIAGNOSTICS = CMD_DIAGNOSTICS
    CMD_PREFERENCES = CMD_PREFERENCES
    CMD_PRESSBRAKE_2D = CMD_PRESSBRAKE_2D

    def register_commands(self) -> None:
        """Delegate to module-level idempotent command registration."""
        register_commands()

    def Initialize(self) -> None:
        """Initialize workbench UI components (commands, menus, toolbars)."""
        # C2-2: Register commands BEFORE appendMenu / appendToolbar
        self.register_commands()

        # On the experimental 2D branch, show only the new UI in the primary
        # menu and toolbar. Legacy commands remain registered (not deleted),
        # but are intentionally not exposed in this workbench layout.
        self.menu_items = [self.CMD_PRESSBRAKE_2D]
        if hasattr(self, "appendMenu"):
            self.appendMenu("PressBrakeSim 2D", self.menu_items)

        self.toolbar_items = [self.CMD_PRESSBRAKE_2D]
        if hasattr(self, "appendToolbar"):
            self.appendToolbar("PressBrakeSim 2D", self.toolbar_items)

    def Activated(self) -> None:
        """Called when SMBS workbench is activated."""
        pass

    def Deactivated(self) -> None:
        """Called when SMBS workbench is deactivated."""
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench") and not _WORKBENCH_REGISTERED:
    try:
        wbs = FreeCADGui.listWorkbenches() if hasattr(FreeCADGui, "listWorkbenches") else {}
        if "SMBSWorkbench" not in wbs:
            FreeCADGui.addWorkbench(SMBSWorkbench())
        _WORKBENCH_REGISTERED = True
    except Exception:
        pass
