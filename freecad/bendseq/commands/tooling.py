"""FreeCAD command embedding the real tooling library in the shared Task Panel."""
from __future__ import annotations


from freecad.bendseq.ui.localization import tr

from freecad.bendseq.icon_theme import icon_path
ICON_PATH = icon_path("btn_tooling_library.svg")


class ToolingCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": tr("tooling_library"),
            "ToolTip": tr("punches_dies_frame_backgauge_and_clamps"),
        }

    def IsActive(self):
        return True

    def Activated(self):
        from freecad.bendseq.commands.pressbrake_2d import (
            PressBrake2DCommand, get_workspace_widget,
            activate_task_dialog, embed_task_dialog,
        )
        from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary
        from freecad.bendseq.ui.tooling_panel import ToolingPanel
        from freecad.bendseq.ui.dialogs import show_error

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
                workspace, panel, tr("library"), "full_tooling_library"
            )
        except Exception as exc:
            show_error(tr("pressbrakesim_tooling_9140dded"), str(exc))


class ToolingSettingsCommand:
    label = ""
    action = ""
    icon = "btn_tooling_library.svg"

    def GetResources(self):
        return {"MenuText": tr(self.label), "ToolTip": tr(self.label),
                "Pixmap": str(icon_path(self.icon))}

    def IsActive(self):
        return True

    def Activated(self):
        from freecad.bendseq.commands.pressbrake_2d import (
            PressBrake2DCommand, get_workspace_widget, _qt,
            activate_task_dialog, embed_task_dialog,
        )
        from freecad.bendseq.ui.tooling_panel import ToolingPanel
        from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary

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
            if activate_task_dialog(workspace, "virtual_clamps"):
                return
            panel = ToolingPanel(library, parent=workspace, clamps_only=True)
            embed_task_dialog(workspace, panel, tr("virtual_clamps"), "virtual_clamps")
            return
        _, _, qt = _qt()
        catalog = library.list_tools()
        kinds = (("punch", catalog.punches), ("die", catalog.dies))
        tools = {f"{tr(kind)} · {tool.tool_id}": tool
                 for kind, entries in kinds for tool in entries}
        if not tools:
            qt.QMessageBox.information(workspace, tr(self.label), tr("add_tooling_to_the_library"))
            return
        selected, accepted = qt.QInputDialog.getItem(
            workspace, tr(self.label), tr("tool"), list(tools), 0, False,
        )
        if accepted:
            panel = ToolingPanel(library, parent=workspace)
            panel._on_edit_segments(tools[selected])
            panel.deleteLater()


class SegmentStockCommand(ToolingSettingsCommand):
    label = "segment_inventory"
    icon = "btn_segment_inventory.svg"
    action = "stock"


class VirtualClampsCommand(ToolingSettingsCommand):
    label = "virtual_clamps"
    icon = "btn_virtual_clamps.svg"
    action = "clamps"


class ToolLayoutCommand(ToolingSettingsCommand):
    label = "calculate_tool_layout"
    icon = "btn_tool_layout.svg"
    action = "layout"
