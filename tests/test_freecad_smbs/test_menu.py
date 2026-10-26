"""
Unit tests for SMBS workbench menu, toolbar, and commands (Commit C3 evolution).
Validates active vs disabled command status and command resources.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.open_run import (
    OpenRunCommand,
    get_current_run,
    set_current_run,
)
from freecad_smbs.commands.simulate import SimulateCommand
from freecad_smbs.commands.tooling import ToolingCommand
from freecad_smbs.commands.verify import VerifyCommand
from freecad_smbs.InitGui import (
    CMD_CALCULATE,
    CMD_INFO,
    CMD_OPEN_RUN,
    CMD_SIMULATE,
    CMD_TOOLING,
    CMD_VERIFY,
    COMMANDS_MAP,
    SMBSWorkbench,
)


class TestSMBSMenuAndCommands(unittest.TestCase):
    def setUp(self):
        set_current_run(None)

    def tearDown(self):
        set_current_run(None)

    def test_open_run_active(self):
        """OpenRunCommand is active (C3 implementation)."""
        cmd = OpenRunCommand()
        self.assertTrue(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Открыть run…")
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_calculate_active(self):
        """CalculateCommand active state depends on current_run (C4 implementation)."""
        cmd = CalculateCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Расчёт")
        self.assertNotIn("S.69 C4", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

        # When a run is loaded, CalculateCommand becomes active
        set_current_run(MagicMock())
        self.assertTrue(cmd.IsActive())

    def test_simulate_active(self):
        """SimulateCommand active state depends on current_run (S.70 implementation)."""
        cmd = SimulateCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Симуляция")
        self.assertNotIn("S.70", res["ToolTip"])  # tooltip no longer references future session
        self.assertTrue(Path(res["Pixmap"]).exists())
        set_current_run(MagicMock())
        self.assertTrue(cmd.IsActive())

    def test_tooling_active(self):
        """ToolingCommand is active (S.71 implementation; library is repo-relative)."""
        cmd = ToolingCommand()
        self.assertTrue(cmd.IsActive())  # always active, no current_run dependency
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Оснастка")
        self.assertNotIn("S.71", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_verify_active(self):
        """VerifyCommand active state depends on current_run (C3 implementation)."""
        cmd = VerifyCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Верификация")
        self.assertTrue(Path(res["Pixmap"]).exists())

        # When a run is loaded, VerifyCommand becomes active
        set_current_run(MagicMock())
        self.assertTrue(cmd.IsActive())

    def test_info_active(self):
        """InfoCommand active state depends on current_run (C3 implementation)."""
        cmd = InfoCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Инфо о run")
        self.assertTrue(Path(res["Pixmap"]).exists())

        # When a run is loaded, InfoCommand becomes active
        set_current_run(MagicMock())
        self.assertTrue(cmd.IsActive())

    def test_menu_registration(self):
        wb = SMBSWorkbench()
        wb.Initialize()

        expected_menu = [
            CMD_OPEN_RUN,
            "Separator",
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            "Separator",
            CMD_VERIFY,
            CMD_INFO,
        ]
        self.assertEqual(wb.menu_items, expected_menu)

        expected_toolbar = [
            CMD_OPEN_RUN,
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            CMD_VERIFY,
            CMD_INFO,
        ]
        self.assertEqual(wb.toolbar_items, expected_toolbar)

        for cmd_name in expected_toolbar:
            self.assertIn(cmd_name, COMMANDS_MAP)
