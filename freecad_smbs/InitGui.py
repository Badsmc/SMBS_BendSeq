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

from freecad_smbs.integration.dxf_manual import DxfManualCommand

CMD_PRESSBRAKE_2D = "SMBS_PressBrake2D"
CMD_TOOLING = "SMBS_Tooling"  # Preserve existing standalone library command
CMD_DXF_MANUAL = "SMBS_DxfManual"
CMD_LOAD_PART = "SMBS_LoadPart"
CMD_TOOLING_TAB = "SMBS_ToolingTab"
CMD_SEQUENCE_CATALOG = "SMBS_SequenceCatalog"
CMD_BACKGAUGE = "SMBS_Backgauge"
CMD_MANUAL = "SMBS_Manual"
CMD_SETTINGS = "SMBS_Settings"
COMMANDS_MAP = {
    CMD_PRESSBRAKE_2D: PressBrake2DCommand,
    CMD_DXF_MANUAL: DxfManualCommand,
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
    "SMBS_ToolLayout",
]
_REGISTERED_COMMANDS: set[str] = set()


def register_commands() -> None:
    if FreeCADGui is None:
        return
    from freecad_smbs.commands.pressbrake_2d import _qt
    from smbs_core.kinematics2d.developer_debug_log import set_debug_log_enabled
    core, _, _ = _qt()
    preferences = core.QSettings("SMBS", "BendSeq")
    set_debug_log_enabled(preferences.value("debug_log/enabled", False, type=bool))
    registered = set(FreeCADGui.listCommands()) if hasattr(FreeCADGui, "listCommands") else set()
    for name, cls in COMMANDS_MAP.items():
        if name in registered or name in _REGISTERED_COMMANDS:
            _REGISTERED_COMMANDS.add(name)
            continue
        # Do not swallow registration errors: broken installations must fail visibly.
        FreeCADGui.addCommand(name, cls())
        _REGISTERED_COMMANDS.add(name)


class _SMBSAutoOpenObserver:
    """Auto-resume an embedded SMBS session when FreeCAD finishes opening it."""

    def __init__(self, workbench):
        self.workbench = workbench

    def slotFinishOpenDocument(self, document):
        owner = self.workbench
        if owner is not None:
            owner._schedule_saved_document(document)


class SMBSWorkbench(Workbench):
    MenuText = "PressBrakeSim"
    ToolTip = "SheetMetal press-brake bending preview and tooling"
    Icon = str(_ICON_ROOT / "gui_workbench.svg")

    def Initialize(self) -> None:
        register_commands()
        # ManualMode: never register automatic sequence search commands.
        # Hand-picked bend orders are checked explicitly by the operator.
        toolbar = [
            CMD_DXF_MANUAL, CMD_TOOLING_TAB, CMD_PRESSBRAKE_2D,
            CMD_SETTINGS,
        ]
        menu = [
            *toolbar, CMD_SEQUENCE_CATALOG,
            *_TOOLING_ACTIONS,
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
        # On switching to SMBS, restore the active saved document even when
        # it was originally opened using a different FreeCAD workbench.
        import FreeCAD as App
        if getattr(self, "_smbs_open_observer", None) is None:
            observer = _SMBSAutoOpenObserver(self)
            App.addDocumentObserver(observer)
            self._smbs_open_observer = observer
        self._schedule_saved_document(getattr(App, "ActiveDocument", None))

    def _schedule_saved_document(self, document):
        if getattr(self, "_smbs_open_observer", None) is None:
            return
        name = getattr(document, "Name", None)
        if not name:
            return
        from freecad_smbs.commands.pressbrake_2d import _qt
        QtCore, _QtGui, _QtWidgets = _qt()
        QtCore.QTimer.singleShot(0, lambda: self._resume_saved_document(name))

    def _resume_saved_document(self, name):
        if getattr(self, "_smbs_open_observer", None) is None:
            return
        import FreeCAD as App
        from freecad_smbs.commands.pressbrake_2d import (
            get_workspace_widget, saved_session_unfold_selection,
        )
        document = App.getDocument(name)
        active = getattr(App, "ActiveDocument", None)
        if (document is None or active is None
                or getattr(active, "Name", None) != name):
            return
        workspace = get_workspace_widget()
        if (workspace is not None and workspace.session is not None
                and getattr(workspace, "_smbs_document_name", None) == name):
            return
        try:
            unfold = saved_session_unfold_selection(document)
        except Exception as exc:
            App.Console.PrintWarning(
                "SMBS: FCStd автозагрузка отклонена: " + str(exc) + "\\n"
            )
            return
        if unfold:
            # The command builds native FreeCAD Tasks/MDI once; load_part()
            # validates the saved geometry and restores all settings.
            try:
                PressBrake2DCommand().Activated(
                    document=document, selection=unfold,
                )
            except Exception as exc:
                App.Console.PrintWarning(
                    "SMBS: ошибка автоматического восстановления FCStd: "
                    + str(exc) + "\\n"
                )

    def Deactivated(self) -> None:
        observer = getattr(self, "_smbs_open_observer", None)
        self._smbs_open_observer = None
        if observer is not None:
            import FreeCAD as App
            App.removeDocumentObserver(observer)

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench"):
    if "SMBSWorkbench" not in (FreeCADGui.listWorkbenches() if hasattr(FreeCADGui, "listWorkbenches") else {}):
        FreeCADGui.addWorkbench(SMBSWorkbench())
