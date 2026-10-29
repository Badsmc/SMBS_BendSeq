"""
InitGui.py - GUI initialization for FreeCAD SMBS Workbench (HandMod-V.0.0.1).

Primary entry: «Ручная последовательность» (manual bend order).
Auto-search command kept for reference but not primary.
"""
from __future__ import annotations

from typing import List

try:
    import FreeCAD
    import FreeCADGui
    from FreeCADGui import Workbench
except ImportError:
    FreeCAD = None
    FreeCADGui = None
    Workbench = object  # type: ignore[assignment,misc]

import sys
from pathlib import Path

try:
    if "__file__" in globals():
        _wb_dir = Path(__file__).resolve().parent
    else:
        import freecad_smbs
        _wb_dir = Path(freecad_smbs.__file__).resolve().parent

    _repo_root = None
    _marker = _wb_dir / ".smbs_repo_path"
    if _marker.exists():
        try:
            _p = Path(_marker.read_text(encoding="utf-8").strip())
            if _p.is_dir() and (_p / "smbs_core").is_dir():
                _repo_root = str(_p.resolve())
        except Exception:
            pass

    if not _repo_root:
        _repo_root = str(_wb_dir.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
except Exception:
    pass

from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.diagnostics import DiagnosticsCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.manual_sequence import ManualSequenceCommand
from freecad_smbs.commands.open_run import OpenRunCommand
from freecad_smbs.commands.preferences import PreferencesCommand
from freecad_smbs.commands.run_active import RunActiveCommand
from freecad_smbs.commands.simulate import SimulateCommand
from freecad_smbs.commands.tooling import ToolingCommand
from freecad_smbs.commands.verify import VerifyCommand

CMD_MANUAL: str = "SMBS_ManualSequence"
CMD_RUN_ACTIVE: str = "SMBS_RunActive"
CMD_OPEN_RUN: str = "SMBS_OpenRun"
CMD_CALCULATE: str = "SMBS_Calculate"
CMD_SIMULATE: str = "SMBS_Simulate"
CMD_TOOLING: str = "SMBS_Tooling"
CMD_VERIFY: str = "SMBS_Verify"
CMD_INFO: str = "SMBS_Info"
CMD_DIAGNOSTICS: str = "SMBS_Diagnostics"
CMD_PREFERENCES: str = "SMBS_Preferences"

COMMANDS_MAP = {
    CMD_MANUAL: ManualSequenceCommand,
    CMD_RUN_ACTIVE: RunActiveCommand,
    CMD_OPEN_RUN: OpenRunCommand,
    CMD_CALCULATE: CalculateCommand,
    CMD_SIMULATE: SimulateCommand,
    CMD_TOOLING: ToolingCommand,
    CMD_VERIFY: VerifyCommand,
    CMD_INFO: InfoCommand,
    CMD_DIAGNOSTICS: DiagnosticsCommand,
    CMD_PREFERENCES: PreferencesCommand,
}


def register_commands() -> None:
    if FreeCADGui is None or not hasattr(FreeCADGui, "addCommand"):
        return
    for cmd_name, cmd_cls in COMMANDS_MAP.items():
        try:
            FreeCADGui.addCommand(cmd_name, cmd_cls())
        except Exception:
            pass


class SMBSWorkbench(Workbench):  # type: ignore[misc]
    """FreeCAD GUI Workbench for SMBS HandMod."""

    MenuText = "SMBS HandMod"
    ToolTip = "Sheet Metal Bending Sequence — Manual Mode"
    Icon = ""

    CMD_MANUAL = "SMBS_ManualSequence"
    CMD_RUN_ACTIVE = "SMBS_RunActive"
    CMD_OPEN_RUN = "SMBS_OpenRun"
    CMD_CALCULATE = "SMBS_Calculate"
    CMD_SIMULATE = "SMBS_Simulate"
    CMD_TOOLING = "SMBS_Tooling"
    CMD_VERIFY = "SMBS_Verify"
    CMD_INFO = "SMBS_Info"
    CMD_DIAGNOSTICS = "SMBS_Diagnostics"
    CMD_PREFERENCES = "SMBS_Preferences"

    def register_commands(self) -> None:
        try:
            import FreeCADGui
        except ImportError:
            FreeCADGui = None

        if FreeCADGui is None or not hasattr(FreeCADGui, "addCommand"):
            return

        from freecad_smbs.commands.calculate import CalculateCommand
        from freecad_smbs.commands.diagnostics import DiagnosticsCommand
        from freecad_smbs.commands.info import InfoCommand
        from freecad_smbs.commands.manual_sequence import ManualSequenceCommand
        from freecad_smbs.commands.open_run import OpenRunCommand
        from freecad_smbs.commands.preferences import PreferencesCommand
        from freecad_smbs.commands.run_active import RunActiveCommand
        from freecad_smbs.commands.simulate import SimulateCommand
        from freecad_smbs.commands.tooling import ToolingCommand
        from freecad_smbs.commands.verify import VerifyCommand

        commands_map = {
            self.CMD_MANUAL: ManualSequenceCommand,
            self.CMD_RUN_ACTIVE: RunActiveCommand,
            self.CMD_OPEN_RUN: OpenRunCommand,
            self.CMD_CALCULATE: CalculateCommand,
            self.CMD_SIMULATE: SimulateCommand,
            self.CMD_TOOLING: ToolingCommand,
            self.CMD_VERIFY: VerifyCommand,
            self.CMD_INFO: InfoCommand,
            self.CMD_DIAGNOSTICS: DiagnosticsCommand,
            self.CMD_PREFERENCES: PreferencesCommand,
        }

        for cmd_name, cmd_cls in commands_map.items():
            try:
                FreeCADGui.addCommand(cmd_name, cmd_cls())
            except Exception:
                pass

    def Initialize(self) -> None:
        self.register_commands()

        # HandMod: manual first
        self.menu_items = [
            self.CMD_MANUAL,
            "Separator",
            self.CMD_RUN_ACTIVE,
            self.CMD_OPEN_RUN,
            "Separator",
            self.CMD_CALCULATE,
            self.CMD_SIMULATE,
            self.CMD_TOOLING,
            "Separator",
            self.CMD_VERIFY,
            self.CMD_INFO,
            "Separator",
            self.CMD_DIAGNOSTICS,
            self.CMD_PREFERENCES,
        ]
        if hasattr(self, "appendMenu"):
            self.appendMenu(["SMBS"], self.menu_items)

        self.toolbar_items = [
            self.CMD_MANUAL,
            self.CMD_RUN_ACTIVE,
            self.CMD_OPEN_RUN,
            self.CMD_CALCULATE,
            self.CMD_SIMULATE,
            self.CMD_TOOLING,
            self.CMD_VERIFY,
            self.CMD_INFO,
            self.CMD_DIAGNOSTICS,
            self.CMD_PREFERENCES,
        ]
        if hasattr(self, "appendToolbar"):
            self.appendToolbar("SMBS", self.toolbar_items)

    def Activated(self) -> None:
        pass

    def Deactivated(self) -> None:
        pass

    def GetClassName(self) -> str:
        return "Gui::PythonWorkbench"


if FreeCADGui is not None and hasattr(FreeCADGui, "addWorkbench"):
    try:
        FreeCADGui.addWorkbench(SMBSWorkbench())
    except Exception:
        pass
