"""
test_bridge_pipeline.py - Unit tests for programmatic run_pipeline_on_fcstd in smbs_bridge.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from freecad_smbs.integration.smbs_bridge import (
    PipelineRunResult,
    run_pipeline_on_fcstd,
)


class MockSuccessCoordinator:
    def __init__(self, workspace_root=None):
        self.workspace_root = workspace_root

    def start_run(self, **kwargs):
        return {
            "status": "SUCCESS",
            "run_id": "run_mock_success",
            "run_dir": "/tmp/mock_run_success",
            "planning_status": "SUCCESS",
            "verification_status": "VERIFIED",
            "production_eligible": True,
            "sequence": [
                {"step": 1, "bend_id": "BEND_01", "direction": "UP", "angle": 90.0}
            ],
            "run_report": {"diagnostics": []},
        }


class MockNoSolutionCoordinator:
    def __init__(self, workspace_root=None):
        self.workspace_root = workspace_root

    def start_run(self, **kwargs):
        return {
            "status": "NO_SOLUTION",
            "run_id": "run_mock_nosol",
            "run_dir": "/tmp/mock_run_nosol",
            "planning_status": "NO_SOLUTION",
            "verification_status": "VERIFICATION_FAILED",
            "production_eligible": False,
            "sequence": [],
            "run_report": {
                "diagnostics": [
                    "Flange height 10.0mm interferes with punch puancon during stroke"
                ]
            },
        }


class MockErrorCoordinator:
    def __init__(self, workspace_root=None):
        self.workspace_root = workspace_root

    def start_run(self, **kwargs):
        raise RuntimeError("Unfold failure in SheetMetal adapter")


class TestBridgePipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_pipeline_"))
        self.dummy_fcstd = self.tmp_dir / "test_part.FCStd"
        self.dummy_fcstd.write_text("dummy fcstd content")

    def test_run_pipeline_success(self):
        result = run_pipeline_on_fcstd(
            self.dummy_fcstd,
            coordinator_cls=MockSuccessCoordinator,
        )
        self.assertIsInstance(result, PipelineRunResult)
        self.assertEqual(result.planning_status, "SUCCESS")
        self.assertEqual(result.verification_status, "VERIFIED")
        self.assertTrue(result.production_eligible)
        self.assertEqual(len(result.candidate_sequence), 1)
        self.assertEqual(result.candidate_sequence[0]["bend_id"], "BEND_01")
        self.assertEqual(result.part_id, "test_part")
        self.assertEqual(result.run_dir, Path("/tmp/mock_run_success"))
        self.assertIsNone(result.error_message)

    def test_run_pipeline_no_solution(self):
        result = run_pipeline_on_fcstd(
            self.dummy_fcstd,
            coordinator_cls=MockNoSolutionCoordinator,
        )
        self.assertIsInstance(result, PipelineRunResult)
        self.assertEqual(result.planning_status, "NO_SOLUTION")
        self.assertEqual(result.verification_status, "VERIFICATION_FAILED")
        self.assertFalse(result.production_eligible)
        self.assertEqual(len(result.candidate_sequence), 0)
        self.assertIsNotNone(result.error_message)
        self.assertIn("interferes with punch", result.error_message)

    def test_run_pipeline_coordinator_exception(self):
        result = run_pipeline_on_fcstd(
            self.dummy_fcstd,
            coordinator_cls=MockErrorCoordinator,
        )
        self.assertIsInstance(result, PipelineRunResult)
        self.assertEqual(result.planning_status, "ERROR")
        self.assertIn("Unfold failure", str(result.error_message))
        self.assertEqual(len(result.candidate_sequence), 0)

    def test_run_pipeline_missing_file_controlled_error(self):
        nonexistent = self.tmp_dir / "nonexistent.FCStd"
        result = run_pipeline_on_fcstd(nonexistent)
        self.assertIsInstance(result, PipelineRunResult)
        self.assertEqual(result.planning_status, "ERROR")
        self.assertIn("не найден", str(result.error_message))
        self.assertEqual(len(result.candidate_sequence), 0)

    def test_run_pipeline_with_tooling_set(self):
        recorded_kwargs = {}

        class MockCapturingCoordinator:
            def __init__(self, workspace_root=None):
                pass

            def start_run(self, **kwargs):
                nonlocal recorded_kwargs
                recorded_kwargs = kwargs
                return {
                    "status": "SUCCESS",
                    "planning_status": "SUCCESS",
                    "verification_status": "VERIFIED",
                    "production_eligible": True,
                    "sequence": [],
                    "run_report": {"diagnostics": []},
                }

        result = run_pipeline_on_fcstd(
            self.dummy_fcstd,
            tooling_set_id="my_production_set",
            coordinator_cls=MockCapturingCoordinator,
        )
        self.assertEqual(result.planning_status, "SUCCESS")
        self.assertIn("machine_config", recorded_kwargs)
        self.assertEqual(
            recorded_kwargs["machine_config"]["tooling_set"],
            "my_production_set",
        )



if __name__ == "__main__":
    unittest.main()
