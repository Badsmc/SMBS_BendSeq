"""PressBrakeSim 2.5D — single FreeCAD workbench entry point.

GUI/v0.0.1 is a dedicated FreeCAD interface development snapshot.
Preserves the working bend search, tooling and machine simulation commands.
No alternate legacy GUI registers on startup.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import FreeCADGui
    from FreeCADGui import Workbench
except ImportError:
    FreeCADGui = None
    Workbench = object

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ICON_ROOT = Path(__file__).resolve().parent / "resources" / "icons"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.commands.pressbrake_2d import (
    PressBrake2DCommand, LoadPartCommand, ToolingTabCommand,
    CalculateCommand, PlanarCandidatesCommand, SequenceCatalogCommand,
    BackgaugeCommand, ManualCommand, SettingsCommand,
)
from freecad_smbs.commands.tooling import ToolingCommand

CMD_PRESSBRAKE_2D = "SMBS_PressBrake2D"
CMD_TOOLING = "SMBS_Tooling"  # Preserve existing standalone library command
CMD_LOAD_PART = "SMBS_LoadPart"
CMD_TOOLING_TAB = "SMBS_ToolingTab"
CMD_CALCULATE = "SMBS_Calculate"
CMD_PLANAR_CANDIDATES = "SMBS_PlanarCandidates"
CMD_SEQUENCE_CATALOG = "SMBS_SequenceCatalog"
CMD_BACKGAUGE = "SMBS_Backgauge"
CMD_MANUAL = "SMBS_Manual"
CMD_SETTINGS = "SMBS_Settings"
COMMANDS_MAP = {
    CMD_PRESSBRAKE_2D: PressBrake2DCommand,
    CMD_LOAD_PART: LoadPartCommand,
    CMD_TOOLING_TAB: ToolingTabCommand,
    CMD_CALCULATE: CalculateCommand,
    CMD_PLANAR_CANDIDATES: PlanarCandidatesCommand,
    CMD_SEQUENCE_CATALOG: SequenceCatalogCommand,
    CMD_BACKGAUGE: BackgaugeCommand,
    CMD_MANUAL: ManualCommand,
    CMD_SETTINGS: SettingsCommand,
    CMD_TOOLING: ToolingCommand,
}
_REGISTERED_COMMANDS: set[str] = set()


def register_commands() -> None:
    if FreeCADGui is None:
        return
    registered = set(FreeCADGui.listCommands()) if hasattr(FreeCADGui, "listCommands") else set()
    for name, cls in COMMANDS_MAP.items():
        if name in registered or name in _REGISTERED_COMMANDS:
            _REGISTERED_COMMANDS.add(name)
            continue
        # Do not swallow registration errors: broken installations must fail visibly.
        FreeCADGui.addCommand(name, cls())
        _REGISTERED_COMMANDS.add(name)


class SMBSWorkbench(Workbench):
    MenuText = "PressBrakeSim 2.5D"
    ToolTip = "SheetMetal press-brake bending preview and tooling"
    Icon = str(_ICON_ROOT / "gui_workbench.svg")

    def Initialize(self) -> None:
        register_commands()
        commands = [
            CMD_PRESSBRAKE_2D, CMD_LOAD_PART, CMD_TOOLING_TAB,
            CMD_CALCULATE, CMD_PLANAR_CANDIDATES, CMD_SEQUENCE_CATALOG,
            CMD_BACKGAUGE, CMD_MANUAL, CMD_SETTINGS,
        ]
        self.appendMenu("PressBrakeSim 2.5D", commands + [CMD_TOOLING])
        self.appendToolbar("PressBrakeSim 2.5D", commands)

    def Activated(self) -> None:
        pass

    def Deactivated(self) -> None:
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench"):
    if "SMBSWorkbench" not in (FreeCADGui.listWorkbenches() if hasattr(FreeCADGui, "listWorkbenches") else {}):
        FreeCADGui.addWorkbench(SMBSWorkbench())
