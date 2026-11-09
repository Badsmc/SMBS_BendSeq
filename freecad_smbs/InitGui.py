"""PressBrakeSim 2.5D — single FreeCAD workbench entry point.

The 'gui' branch owns interface development; 2D kinematics are maintained separately.
Only the supported simulation and tooling commands are registered.
Legacy SMBS solvers and panels are not imported during startup.
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
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freecad_smbs.commands.pressbrake_2d import PressBrake2DCommand
from freecad_smbs.commands.tooling import ToolingCommand

CMD_PRESSBRAKE_2D = "SMBS_PressBrake2D"
CMD_TOOLING = "SMBS_Tooling"
COMMANDS_MAP = {
    CMD_PRESSBRAKE_2D: PressBrake2DCommand,
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
    Icon = ""

    def Initialize(self) -> None:
        register_commands()
        commands = [CMD_PRESSBRAKE_2D, CMD_TOOLING]
        self.appendMenu("PressBrakeSim 2.5D", commands)
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
