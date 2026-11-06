"""Standalone active 2.5D tooling library command (no legacy solver bridge)."""
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
        from freecad_smbs.resources.tooling_paths import TOOL_LIBRARY_ROOT
        from smbs_core.geometry_backend.io.tooling_library import ToolingLibrary
        from freecad_smbs.ui.tooling_panel import ToolingPanel
        from freecad_smbs.ui.dialogs import show_error
        try:
            library = ToolingLibrary(TOOL_LIBRARY_ROOT)
            ToolingPanel(library).exec()
        except Exception as exc:
            show_error("PressBrakeSim: оснастка", str(exc))
