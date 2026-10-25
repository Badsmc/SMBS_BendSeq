"""
Unit tests for SMBS workbench menu, toolbar, and command stubs (Commit C2).
Validates per-command IsActive status and command resources.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.open_run import OpenRunCommand
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
    def test_open_run_stub_disabled(self):
        cmd = OpenRunCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Открыть run…")
        self.assertIn("S.69 C3", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_calculate_stub_disabled(self):
        cmd = CalculateCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Расчёт")
        self.assertIn("S.69 C4", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_simulate_is_disabled(self):
        cmd = SimulateCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Симуляция")
        self.assertIn("S.70", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_tooling_is_disabled(self):
        cmd = ToolingCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Оснастка")
        self.assertIn("S.71", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_verify_stub_disabled(self):
        cmd = VerifyCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Верификация")
        self.assertIn("S.69 C3", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_info_stub_disabled(self):
        cmd = InfoCommand()
        self.assertFalse(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Инфо о run")
        self.assertIn("S.69 C3", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    def test_menu_registration(self):
        wb = SMBSWorkbench()
        wb.Initialize()

        # Check menu items structure
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

        # Check toolbar items structure
        expected_toolbar = [
            CMD_OPEN_RUN,
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            CMD_VERIFY,
            CMD_INFO,
        ]
        self.assertEqual(wb.toolbar_items, expected_toolbar)

        # Check all commands mapped in COMMANDS_MAP
        for cmd_name in expected_toolbar:
            self.assertIn(cmd_name, COMMANDS_MAP)
