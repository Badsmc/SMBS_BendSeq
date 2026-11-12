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
        # Six native FreeCAD toolbar commands. Other actions remain available
        # through the workbench menu, without duplicating the toolbar buttons.
        # Simulation is a viewer/action, not an implicit sequence search.
        # Preserve the original physical automatic search in the workbench MENU
        # under "Расчёт последовательности", separate from quick preview.
        toolbar = [
            CMD_LOAD_PART, CMD_TOOLING_TAB, CMD_PRESSBRAKE_2D,
            CMD_PLANAR_CANDIDATES, CMD_MANUAL, CMD_SETTINGS,
        ]
        menu = [
            *toolbar, CMD_CALCULATE, CMD_SEQUENCE_CATALOG,
            CMD_BACKGAUGE, CMD_TOOLING,
        ]
        self.appendMenu("PressBrakeSim 2.5D", menu)
        self.appendToolbar("PressBrakeSim 2.5D", toolbar)

    def Activated(self) -> None:
        # FreeCAD's own QToolBar remains the command host. Only this addon's
        # toolbar gets compact labels beside small neutral icons, so the six
        # actions are recognizable without memorizing their pictograms.
        # No application-level QStyle or QPalette changes are made.
        if FreeCADGui is None:
            return
        from freecad_smbs.commands.pressbrake_2d import _qt
        QtCore, _QtGui, QtWidgets = _qt()

        def configure_native_toolbar():
            main = FreeCADGui.getMainWindow()
            if main is None:
                return
            for toolbar in main.findChildren(QtWidgets.QToolBar):
                if (
                    toolbar.objectName() == "PressBrakeSim 2.5D"
                    or toolbar.windowTitle() == "PressBrakeSim 2.5D"
                ):
                    toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                    toolbar.setIconSize(QtCore.QSize(18, 18))

        # FreeCAD can finalize its workbench toolbars after Activated().
        QtCore.QTimer.singleShot(0, configure_native_toolbar)

    def Deactivated(self) -> None:
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench"):
    if "SMBSWorkbench" not in (FreeCADGui.listWorkbenches() if hasattr(FreeCADGui, "listWorkbenches") else {}):
        FreeCADGui.addWorkbench(SMBSWorkbench())
