"""FreeCAD workbench for manual bend orders, tooling, and Coin3D simulation."""
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
    SequenceCatalogCommand,
    BackgaugeCommand, ManualCommand, SettingsCommand,
)
from freecad_smbs.commands.tooling import (
    ToolingCommand, SegmentStockCommand, VirtualClampsCommand, ToolLayoutCommand,
)

CMD_PRESSBRAKE_2D = "SMBS_PressBrake2D"
CMD_TOOLING = "SMBS_Tooling"  # Preserve existing standalone library command
CMD_LOAD_PART = "SMBS_LoadPart"
CMD_TOOLING_TAB = "SMBS_ToolingTab"
CMD_SEQUENCE_CATALOG = "SMBS_SequenceCatalog"
CMD_BACKGAUGE = "SMBS_Backgauge"
CMD_MANUAL = "SMBS_Manual"
CMD_SETTINGS = "SMBS_Settings"
COMMANDS_MAP = {
    CMD_PRESSBRAKE_2D: PressBrake2DCommand,
    CMD_LOAD_PART: LoadPartCommand,
    CMD_TOOLING_TAB: ToolingTabCommand,
    CMD_SEQUENCE_CATALOG: SequenceCatalogCommand,
    CMD_BACKGAUGE: BackgaugeCommand,
    CMD_MANUAL: ManualCommand,
    CMD_SETTINGS: SettingsCommand,
    CMD_TOOLING: ToolingCommand,
    "SMBS_SegmentStock": SegmentStockCommand,
    "SMBS_VirtualClamps": VirtualClampsCommand,
    "SMBS_ToolLayout": ToolLayoutCommand,
}
TOOLING_TOOLBAR = "PressBrakeSim — оснастка"
_TOOLING_ACTIONS = [
    "SMBS_SegmentStock", "SMBS_VirtualClamps", "SMBS_ToolLayout", CMD_BACKGAUGE,
]
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
    MenuText = "PressBrakeSim"
    ToolTip = "SheetMetal press-brake bending preview and tooling"
    Icon = str(_ICON_ROOT / "gui_workbench.svg")

    def Initialize(self) -> None:
        register_commands()
        # ManualMode: never register automatic sequence search commands.
        # Hand-picked bend orders are checked explicitly by the operator.
        toolbar = [
            CMD_LOAD_PART, CMD_TOOLING_TAB, CMD_PRESSBRAKE_2D,
            CMD_MANUAL, CMD_SETTINGS,
        ]
        menu = [
            *toolbar, CMD_SEQUENCE_CATALOG,
            CMD_TOOLING, *_TOOLING_ACTIONS,
        ]
        self.appendMenu(self.MenuText, menu)
        self.appendToolbar(self.MenuText, toolbar)
        self.appendToolbar(TOOLING_TOOLBAR, _TOOLING_ACTIONS)

    def Activated(self) -> None:
        # FreeCAD's own QToolBar remains the command host. Only this addon's
        # toolbar gets compact labels beside small neutral icons, so the
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
                    toolbar.objectName() in (self.MenuText, TOOLING_TOOLBAR)
                    or toolbar.windowTitle() in (self.MenuText, TOOLING_TOOLBAR)
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
