"""
open_run.py - Command to select and load an SMBS run directory.

Uses FreeCAD user preferences (Mod/SMBS/LastRunDir) instead of hardcoded paths.
Maintains module-level _current_run state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from freecad_smbs.integration import smbs_bridge
from freecad_smbs.ui.dialogs import show_error, show_info
from smbs_core.viewer.run_loader import RunArtifacts

ICON_PATH: Path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "open_run.svg"

# Module-level state for current loaded run (C3-5)
_current_run: Optional[RunArtifacts] = None


def get_current_run() -> Optional[RunArtifacts]:
    """Retrieve the currently loaded RunArtifacts, or None if no run is loaded."""
    global _current_run
    return _current_run


def set_current_run(artifacts: Optional[RunArtifacts]) -> None:
    """Set the currently loaded RunArtifacts (useful for commands and unit tests)."""
    global _current_run
    _current_run = artifacts


def get_last_run_dir_pref() -> str:
    """Retrieve LastRunDir from FreeCAD user preferences (C3-1)."""
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            return prefs.GetString("LastRunDir", "")
    except Exception:
        pass
    return ""


def set_last_run_dir_pref(path_str: str) -> None:
    """Save LastRunDir in FreeCAD user preferences (C3-1)."""
    try:
        import FreeCAD
        if FreeCAD is not None and hasattr(FreeCAD, "ParamGet"):
            prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SMBS")
            prefs.SetString("LastRunDir", path_str)
    except Exception:
        pass


class OpenRunCommand:
    """Command to select and load an SMBS run directory."""

    def GetResources(self) -> Dict[str, Any]:
        return {
            "Pixmap": str(ICON_PATH),
            "MenuText": "Открыть run…",
            "ToolTip": "Открыть директорию запуска SMBS с артефактами",
        }

    def Activated(self) -> None:
        last_dir = get_last_run_dir_pref()
        selected_dir = ""

        try:
            from PySide6 import QtWidgets
            selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
                None,
                "Выберите директорию SMBS run",
                last_dir or str(Path.cwd()),
            )
        except Exception:
            # Fallback if invoked outside interactive Qt event loop
            pass

        if not selected_dir:
            return

        try:
            artifacts = smbs_bridge.load_run(selected_dir)
            set_current_run(artifacts)
            set_last_run_dir_pref(selected_dir)
            show_info("SMBS: Загрузка run", f"Успешно загружен run из:\n{selected_dir}")
        except Exception as e:
            show_error("SMBS: Ошибка загрузки", f"Не удалось загрузить run:\n{e}")

    def IsActive(self) -> bool:
        return True
