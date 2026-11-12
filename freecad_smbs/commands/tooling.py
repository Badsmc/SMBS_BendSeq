"""FreeCAD command embedding the real tooling library in the shared Task Panel."""
from __future__ import annotations

from pathlib import Path

ICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "icons" / "tooling.svg"


class ToolingCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Библиотека оснастки",
            "ToolTip": "Пуансоны, матрицы, станина, задний упор, зажимы",
        }

    def IsActive(self):
        return True

    def Activated(self):
        from freecad_smbs.commands.pressbrake_2d import (
            PressBrake2DCommand, get_workspace_widget,
            activate_task_dialog, embed_task_dialog,
        )
        from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT
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
            library = ToolingLibrary(TOOL_LIBRARY_ROOT)
            panel = ToolingPanel(library, parent=workspace)
            embed_task_dialog(
                workspace, panel, "Библиотека", "full_tooling_library"
            )
        except Exception as exc:
            show_error("PressBrakeSim: оснастка", str(exc))
