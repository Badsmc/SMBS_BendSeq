"""
Unit tests for SMBS workbench menu, toolbar, and commands (Commit C3 + RunActive evolution).
Validates active vs disabled command status, command resources, and RunActiveCommand execution.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from freecad_smbs.commands.calculate import CalculateCommand
from freecad_smbs.commands.info import InfoCommand
from freecad_smbs.commands.open_run import (
    OpenRunCommand,
    get_current_run,
    set_current_run,
)
from freecad_smbs.commands.run_active import RunActiveCommand
from freecad_smbs.commands.simulate import SimulateCommand
from freecad_smbs.commands.tooling import ToolingCommand
from freecad_smbs.commands.verify import VerifyCommand
from freecad_smbs.InitGui import (
    CMD_CALCULATE,
    CMD_DIAGNOSTICS,
    CMD_INFO,
    CMD_OPEN_RUN,
    CMD_PREFERENCES,
    CMD_RUN_ACTIVE,
    CMD_SIMULATE,
    CMD_TOOLING,
    CMD_VERIFY,
    COMMANDS_MAP,
    SMBSWorkbench,
)
from freecad_smbs.integration.smbs_bridge import PipelineRunResult


class TestSMBSMenuAndCommands(unittest.TestCase):
    def setUp(self):
        set_current_run(None)

    def tearDown(self):
        set_current_run(None)

    def test_run_active_resources_and_state(self):
        """RunActiveCommand is always active and has valid icon and text."""
        cmd = RunActiveCommand()
        self.assertTrue(cmd.IsActive())
        res = cmd.GetResources()
        self.assertEqual(res["MenuText"], "Рассчитать последовательность")
        self.assertIn("активной детали", res["ToolTip"])
        self.assertTrue(Path(res["Pixmap"]).exists())

    @patch("freecad_smbs.commands.run_active.show_error")
    def test_run_active_no_active_document(self, mock_show_error):
        """If FreeCAD has no active document, shows error dialog."""
        mock_app = MagicMock()
        mock_app.ActiveDocument = None
        with patch.dict("sys.modules", {"FreeCAD": mock_app}):
            cmd = RunActiveCommand()
            cmd.Activated()
        mock_show_error.assert_called_once()
        self.assertIn("Нет активной детали", mock_show_error.call_args[0][0])

    @patch("freecad_smbs.commands.run_active.show_error")
    def test_run_active_unsaved_document(self, mock_show_error):
        """If active document is unsaved, blocks and prompts user to save."""
        mock_doc = MagicMock()
        mock_doc.FileName = ""
        mock_app = MagicMock()
        mock_app.ActiveDocument = mock_doc
        with patch.dict("sys.modules", {"FreeCAD": mock_app}):
            cmd = RunActiveCommand()
            cmd.Activated()
        mock_show_error.assert_called_once()
        self.assertIn("не сохранён", mock_show_error.call_args[0][0])

    @patch("freecad_smbs.commands.run_active.show_pipeline_result")
    @patch("freecad_smbs.commands.run_active.render_flat_pattern_annotations")
    @patch("freecad_smbs.integration.smbs_bridge.get_annotations")
    @patch("freecad_smbs.integration.smbs_bridge.run_pipeline_on_fcstd")
    def test_run_active_success(
        self,
        mock_run_pipeline,
        mock_get_annotations,
        mock_render_annotations,
        mock_show_result,
    ):
        """On success, executes pipeline, binds current run, renders annotations, and shows result."""
        with tempfile.NamedTemporaryFile(suffix=".FCStd") as tf:
            mock_doc = MagicMock()
            mock_doc.FileName = tf.name
            mock_doc.MustExecute = False
            mock_app = MagicMock()
            mock_app.ActiveDocument = mock_doc

            mock_artifacts = MagicMock()
            mock_run_pipeline.return_value = PipelineRunResult(
                run_dir=Path("/fake/run"),
                planning_status="SUCCESS",
                verification_status="VERIFIED",
                production_eligible=True,
                candidate_sequence=[{"step": 1, "bend_id": "b1"}],
                artifacts=mock_artifacts,
            )
            mock_get_annotations.return_value = [MagicMock()]

            with patch.dict("sys.modules", {"FreeCAD": mock_app}):
                cmd = RunActiveCommand()
                cmd.Activated()

            mock_run_pipeline.assert_called_once()
            self.assertEqual(get_current_run(), mock_artifacts)
            mock_render_annotations.assert_called_once()
            mock_show_result.assert_called_once()

    @patch("freecad_smbs.commands.run_active.show_pipeline_result")
    @patch("freecad_smbs.commands.run_active.render_flat_pattern_annotations")
    @patch("freecad_smbs.integration.smbs_bridge.get_annotations")
    @patch("freecad_smbs.integration.smbs_bridge.run_pipeline_on_fcstd")
    def test_run_active_partial_exploratory(
        self,
        mock_run_pipeline,
        mock_get_annotations,
        mock_render_annotations,
        mock_show_result,
    ):
        """When tooling is not specified, runs EXPLORATORY, binds run, renders annotations on PARTIAL."""
        with tempfile.NamedTemporaryFile(suffix=".FCStd") as tf:
            mock_doc = MagicMock()
            mock_doc.FileName = tf.name
            mock_doc.MustExecute = False
            mock_app = MagicMock()
            mock_app.ActiveDocument = mock_doc
            mock_app.ParamGet.return_value.GetString.side_effect = lambda name, default="": default
            mock_app.ParamGet.return_value.GetBool.side_effect = lambda name, default=False: default

            mock_artifacts = MagicMock()
            mock_run_pipeline.return_value = PipelineRunResult(
                run_dir=Path("/fake/run"),
                planning_status="PARTIAL",
                verification_status="VERIFICATION_FAILED",
                production_eligible=False,
                candidate_sequence=[{"step": 1, "bend_id": "b1", "direction": {"value": "UP"}, "angle_deg": {"value": 90.0}}],
                artifacts=mock_artifacts,
            )
            mock_get_annotations.return_value = [MagicMock()]

            with patch.dict("sys.modules", {"FreeCAD": mock_app}):
                cmd = RunActiveCommand()
                cmd.Activated()

            mock_run_pipeline.assert_called_once()
            call_kwargs = mock_run_pipeline.call_args[1]
            self.assertEqual(call_kwargs.get("unknown_policy"), "EXPLORATORY")
            self.assertEqual(get_current_run(), mock_artifacts)
            mock_render_annotations.assert_called_once()
            mock_show_result.assert_called_once()

    @patch("freecad_smbs.ui.dialogs.show_info")
    def test_show_pipeline_result_partial_dialog(self, mock_show_info):
        """show_pipeline_result handles PARTIAL sequence with informative exploratory title."""
        from freecad_smbs.ui.dialogs import show_pipeline_result
        res = PipelineRunResult(
            run_dir=Path("/fake/run"),
            planning_status="PARTIAL",
            verification_status="VERIFICATION_FAILED",
            production_eligible=False,
            candidate_sequence=[{"step": 1, "bend_id": "b1", "direction": "UP", "angle": 90.0}],
        )
        show_pipeline_result(res)
        mock_show_info.assert_called_once()
        self.assertIn("EXPLORATORY", mock_show_info.call_args[0][0])
        self.assertIn("Шаг 1: b1", mock_show_info.call_args[0][1])

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
        self.assertNotIn("S.70", res["ToolTip"])
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
            CMD_RUN_ACTIVE,
            CMD_OPEN_RUN,
            "Separator",
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            "Separator",
            CMD_VERIFY,
            CMD_INFO,
            "Separator",
            CMD_DIAGNOSTICS,
            CMD_PREFERENCES,
        ]
        self.assertEqual(wb.menu_items, expected_menu)

        expected_toolbar = [
            CMD_RUN_ACTIVE,
            CMD_OPEN_RUN,
            CMD_CALCULATE,
            CMD_SIMULATE,
            CMD_TOOLING,
            CMD_VERIFY,
            CMD_INFO,
            CMD_DIAGNOSTICS,
            CMD_PREFERENCES,
        ]
        self.assertEqual(wb.toolbar_items, expected_toolbar)

        for cmd_name in expected_toolbar:
            self.assertIn(cmd_name, COMMANDS_MAP)
