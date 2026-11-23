"""FreeCAD command embedding the real tooling library in the shared Task Panel."""
from __future__ import annotations

from pathlib import Path

from freecad_smbs.ui.localization import tr

from freecad_smbs.icon_theme import icon_path
ICON_PATH = icon_path("tooling.svg")


class ToolingCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": tr("Библиотека оснастки"),
            "ToolTip": tr("Пуансоны, матрицы, станина, задний упор, зажимы"),
        }

    def IsActive(self):
        return True

    def Activated(self):
        from freecad_smbs.commands.pressbrake_2d import (
            PressBrake2DCommand, get_workspace_widget,
            activate_task_dialog, embed_task_dialog,
        )
        from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary
        from freecad_smbs.ui.tooling_panel import ToolingPanel
        from freecad_smbs.ui.dialogs import show_error

        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is None:
            raise RuntimeError("PressBrakeSim workspace did not open")
        if activate_task_dialog(workspace, "full_tooling_library"):
            return
        try:
            library = ToolingLibrary(workspace.tool_library.library_root)
            panel = ToolingPanel(library, parent=workspace)
            embed_task_dialog(
                workspace, panel, tr("Библиотека"), "full_tooling_library"
            )
        except Exception as exc:
            show_error(tr("PressBrakeSim: оснастка"), str(exc))


class ToolingSettingsCommand:
    label = ""
    action = ""

    def GetResources(self):
        return {"MenuText": tr(self.label), "ToolTip": tr(self.label), "Pixmap": str(ICON_PATH)}

    def IsActive(self):
        return True

    def Activated(self):
        from freecad_smbs.commands.pressbrake_2d import (
            PressBrake2DCommand, get_workspace_widget, _qt,
        )
        from freecad_smbs.ui.tooling_panel import ToolingPanel
        from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary

        workspace = get_workspace_widget()
        if workspace is None:
            PressBrake2DCommand().Activated()
            workspace = get_workspace_widget()
        if workspace is None:
            return
        if self.action == "layout":
            workspace._show_manual_tooling_assembly()
            return
        library = ToolingLibrary(workspace.tool_library.library_root)
        if self.action == "clamps":
            panel = ToolingPanel(library, parent=workspace, clamps_only=True)
            run = getattr(panel, "exec", None) or panel.exec_
            run()
            panel.deleteLater()
            return
        _, _, qt = _qt()
        catalog = library.list_tools()
        kinds = (("Пуансон", catalog.punches), ("Матрица", catalog.dies))
        tools = {f"{tr(kind)} · {tool.tool_id}": tool
                 for kind, entries in kinds for tool in entries}
        if not tools:
            qt.QMessageBox.information(workspace, tr(self.label), tr("Добавьте оснастку в библиотеку."))
            return
        selected, accepted = qt.QInputDialog.getItem(
            workspace, tr(self.label), tr("Инструмент"), list(tools), 0, False,
        )
        if accepted:
            panel = ToolingPanel(library, parent=workspace)
            panel._on_edit_segments(tools[selected])
            panel.deleteLater()


class SegmentStockCommand(ToolingSettingsCommand):
    label = "Сегменты и количество"
    action = "stock"


class VirtualClampsCommand(ToolingSettingsCommand):
    label = "Виртуальные зажимы"
    action = "clamps"


class ToolLayoutCommand(ToolingSettingsCommand):
    label = "Рассчитать раскладку"
    action = "layout"
