import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json
import shutil
import hashlib

from smbs_core.workers.solver.worker import SolverWorker
from smbs_core.protocols.file_store import FileStore, RunStatus, TransitionStatus, ConfidenceLevel


class TestSolverWorkerHonest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.workspace_root = self.test_dir / "workspace"
        self.runs_dir = self.workspace_root / "runs"
        self.run_dir = self.runs_dir / "run_test"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.file_store = FileStore()

        # Create dummy geometry brep
        self.brep_file = self.run_dir / "part.brep"
        self.brep_file.write_bytes(b"DUMMY BREP")
        brep_hash = hashlib.sha256(b"DUMMY BREP").hexdigest()

        # Create canonical part json
        self.canonical_part_data = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": "run_test",
            "part_id": "part_1",
            "input_fingerprint": "dummy_fp",
            "producer": {"name": "test", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-17T00:00:00Z",
            "content_hash": "a" * 64,
            "payload": {
                "part_id": "part_1",
                "thickness_mm": {"value": 2.0, "unit": "mm", "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                "material": "ALUMINUM",
                "input_mode": "CANONICAL_PART",
                "reference_panel_id": "P1",
                "geometry_artifact": {
                    "file_path": str(self.brep_file.relative_to(self.workspace_root)),
                    "format": "BREP",
                    "content_hash": brep_hash
                },
                "panels": {
                    "P1": {
                        "panel_id": "P1",
                        "area_mm2": 100.0,
                        "centroid": [0.0, 0.0, 0.0],
                        "normal": [0.0, 0.0, 1.0],
                        "adjacent_bend_ids": ["B1"]
                    },
                    "P2": {
                        "panel_id": "P2",
                        "area_mm2": 100.0,
                        "centroid": [0.0, 50.0, 0.0],
                        "normal": [0.0, 1.0, 0.0],
                        "adjacent_bend_ids": ["B1"]
                    }
                },
                "bends": {
                    "B1": {
                        "bend_id": "B1",
                        "parent_panel_id": "P1",
                        "child_panel_id": "P2",
                        "axis_point": [0.0, 0.0, 0.0],
                        "axis_direction": [1.0, 0.0, 0.0],
                        "angle_deg": {"value": 90.0, "unit": "deg", "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                        "length_mm": {"value": 100.0, "unit": "mm", "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                        "radius_mm": {"value": 2.0, "unit": "mm", "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                        "k_factor": {"value": 0.4, "unit": "dimensionless", "source": "DEFAULT_POLICY", "confidence": "ESTIMATED"},
                        "direction": {"value": "UP", "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"}
                    }
                }
            }
        }
        self.canonical_part_file = self.run_dir / "canonical_part.json"
        cp_hash = self.file_store.write_json_atomic(self.canonical_part_file, self.canonical_part_data, schema_name="canonical_part")

        # Create tooling manifest
        self.tooling_manifest_data = {
            "schema_name": "tooling_manifest",
            "schema_version": "1.1",
            "run_id": "run_test",
            "part_id": "part_1",
            "input_fingerprint": "dummy_fp",
            "producer": {"name": "test", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-17T00:00:00Z",
            "content_hash": "b" * 64,
            "payload": {
                "press": {"press_id": "press_100t", "max_tonnage_tons": 100.0, "bed_length_mm": 2000.0},
                "punch": {"tool_id": "PUNCH_REAL_1", "punch_type": "straight", "height_mm": 100.0, "tip_radius_mm": 1.0, "tip_angle_deg": 88.0, "tooling_status": "SIMPLIFIED"},
                "die": {"tool_id": "DIE_REAL_1", "v_opening_mm": 12.0, "v_angle_deg": 88.0, "height_mm": 80.0, "shoulder_radius_mm": 1.0, "tooling_status": "SIMPLIFIED"}
            }
        }
        self.tooling_manifest_file = self.run_dir / "tooling_manifest.json"
        tm_hash = self.file_store.write_json_atomic(self.tooling_manifest_file, self.tooling_manifest_data, schema_name="tooling_manifest")

        self.cp_hash = cp_hash
        self.tm_hash = tm_hash

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_search_request(self, unknown_policy="PRODUCTION", algorithm="astar"):
        req_data = {
            "schema_name": "search_request",
            "schema_version": "1.1",
            "run_id": "run_test",
            "part_id": "part_1",
            "input_fingerprint": "dummy_fp",
            "producer": {"name": "test", "version": "1.0"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-17T00:00:00Z",
            "content_hash": "c" * 64,
            "payload": {
                "canonical_part_ref": {
                    "file_path": str(self.canonical_part_file.relative_to(self.workspace_root)),
                    "content_hash": self.cp_hash
                },
                "tooling_manifest_ref": {
                    "file_path": str(self.tooling_manifest_file.relative_to(self.workspace_root)),
                    "content_hash": self.tm_hash
                },
                "search_algorithm": algorithm,
                "candidate_order_policy": "outside_in",
                "unknown_policy": unknown_policy,
                "cost_weights": {"w_tool": 100.0, "w_flip": 20.0, "w_setup": 10.0, "w_time": 1.0},
                "resource_budget": {
                    "max_wall_time_s": 30.0,
                    "max_nodes": 1000,
                    "max_cache_entries": 1000,
                    "max_geometry_ops": 500,
                    "max_trajectory_samples": 500,
                    "max_memory_mb": 1024.0
                }
            }
        }
        req_file = self.run_dir / "06_search_request.json"
        self.file_store.write_json_atomic(req_file, req_data, schema_name="search_request")
        return req_file

    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_production_rejects_unknown(self, mock_planner_cls, mock_load_brep):
        """Test that unknown_policy=PRODUCTION aborts with NO_SOLUTION and UNKNOWN_ABORT if step has UNKNOWN."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": True,
            "sequence": [
                {
                    "bend_id": "B1",
                    "collision_status": "UNKNOWN",
                    "planner_status": "VALID",
                    "tooling_status": "SIMPLIFIED"
                }
            ],
            "nodes_explored": 15,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION")
        res_file = worker.execute(req_file)

        res_data = self.file_store.read_json_verified(res_file, expected_schema_name="search_result")
        payload = res_data["payload"]

        self.assertEqual(payload["planning_status"], "NO_SOLUTION")
        self.assertEqual(payload["termination_reason"], "UNKNOWN_ABORT")
        self.assertTrue(any("UNKNOWN_ABORT" in d for d in res_data["diagnostics"]))

    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_exploratory_accepts_unknown_as_partial(self, mock_planner_cls, mock_load_brep):
        """Test that unknown_policy=EXPLORATORY preserves UNKNOWN steps with status=PARTIAL."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": True,
            "sequence": [
                {
                    "bend_id": "B1",
                    "collision_status": "UNKNOWN",
                    "planner_status": "VALID",
                    "tooling_status": "SIMPLIFIED"
                }
            ],
            "nodes_explored": 15,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="EXPLORATORY")
        res_file = worker.execute(req_file)

        res_data = self.file_store.read_json_verified(res_file, expected_schema_name="search_result")
        payload = res_data["payload"]

        self.assertEqual(payload["planning_status"], "PARTIAL")
        self.assertEqual(payload["optimality_claim"], "feasible_unoptimized")
        self.assertEqual(len(payload["candidate_sequence"]), 1)
        self.assertEqual(payload["candidate_sequence"][0]["collision_status"], "UNKNOWN")

    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_no_fabricated_metrics(self, mock_planner_cls, mock_load_brep):
        """Test that solver_metrics, backgauge, and tooling fields are not fabricated."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": True,
            "sequence": [
                {
                    "bend_id": "B1",
                    "collision_status": "VALID",
                    "planner_status": "VALID",
                    "tooling_status": "SIMPLIFIED"
                }
            ],
            "nodes_explored": 42,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION")
        res_file = worker.execute(req_file)

        res_data = self.file_store.read_json_verified(res_file, expected_schema_name="search_result")
        payload = res_data["payload"]
        metrics = payload["solver_metrics"]

        self.assertNotEqual(metrics["peak_memory_mb"], 15.5)
        self.assertEqual(metrics["solver_nodes"], 42)
        step0 = payload["candidate_sequence"][0]
        # Backgauge is UNKNOWN confidence when unconfirmed, not ESTIMATED
        self.assertEqual(step0["backgauge"]["confidence"], "UNKNOWN")
        self.assertIsNone(step0["backgauge"]["x_mm"])
        # Tool ID is taken from tooling_manifest
        self.assertEqual(step0["tool_id"], "PUNCH_REAL_1")

    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.GreedyUnfoldPlanner")
    def test_solver_worker_greedy_dispatches_to_greedy(self, mock_greedy_cls, mock_load_brep):
        """Test that algorithm=greedy instantiates GreedyUnfoldPlanner."""
        mock_greedy = MagicMock()
        mock_greedy.solve.return_value = {
            "success": False,
            "termination_reason": "NO_MORE_CANDIDATES",
            "nodes_explored": 5
        }
        mock_greedy_cls.return_value = mock_greedy

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION", algorithm="greedy")
        worker.execute(req_file)

        mock_greedy_cls.assert_called_once()

    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_rejects_real_step_when_validator_simplified(self, mock_planner_cls, mock_load_brep):
        """KL-20 inv#2/#3: planner step cannot claim REAL tooling when validator executed SIMPLIFIED."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": True,
            "termination_reason": "GOAL_REACHED",
            "sequence": [
                {
                    "bend_id": "B1",
                    "collision_status": "VALID",
                    "planner_status": "VALID",
                    "tooling_status": "REAL"
                }
            ],
            "nodes_explored": 5,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION")
        with self.assertRaisesRegex(ValueError, "tooling_status divergence"):
            worker.execute(req_file)

    @patch("smbs_core.workers.solver.worker.PhysicalValidator")
    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_rejects_simplified_step_when_validator_missing(self, mock_planner_cls, mock_load_brep, mock_validator_cls):
        """KL-20 inv#2/#3: planner step cannot claim SIMPLIFIED tooling when validator executed MISSING."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": True,
            "termination_reason": "GOAL_REACHED",
            "sequence": [
                {
                    "bend_id": "B1",
                    "collision_status": "VALID",
                    "planner_status": "VALID",
                    "tooling_status": "SIMPLIFIED"
                }
            ],
            "nodes_explored": 5,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        mock_validator = MagicMock()
        mock_validator.tooling_status = "MISSING"
        mock_validator_cls.return_value = mock_validator

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION")
        with self.assertRaisesRegex(ValueError, "tooling_status divergence"):
            worker.execute(req_file)

    @patch("smbs_core.workers.solver.worker.PhysicalValidator")
    @patch("smbs_core.workers.solver.worker.SolverWorker._load_brep", return_value=MagicMock())
    @patch("smbs_core.workers.solver.worker.AStarBackwardPlanner")
    def test_solver_worker_emits_diagnostic_on_no_solution_simplified_production(
        self, mock_planner_cls, mock_load_brep, mock_validator_cls
    ):
        """KL-8d: NO_SOLUTION with SIMPLIFIED tooling under PRODUCTION emits actionable diagnostic."""
        mock_planner = MagicMock()
        mock_planner.solve.return_value = {
            "success": False,
            "termination_reason": "NO_MORE_CANDIDATES",
            "sequence": [],
            "nodes_explored": 0,
            "rejections": []
        }
        mock_planner_cls.return_value = mock_planner

        mock_validator = MagicMock()
        mock_validator.tooling_status = "SIMPLIFIED"
        mock_validator_cls.return_value = mock_validator

        worker = SolverWorker(self.file_store)
        req_file = self._create_search_request(unknown_policy="PRODUCTION")
        res_file = worker.execute(req_file)

        res_data = self.file_store.read_json_verified(res_file, expected_schema_name="search_result")
        self.assertEqual(res_data["payload"]["planning_status"], "NO_SOLUTION")
        self.assertEqual(res_data["status"], "NO_SOLUTION")
        expected_msg = (
            "No tooling provided (SIMPLIFIED synthetic); PRODUCTION policy rejects "
            "UNKNOWN collisions. Provide --punch-id/--die-id or --tooling-set/"
            "--tooling-config (or run with EXPLORATORY policy if supported)."
        )
        self.assertIn(expected_msg, res_data["diagnostics"])


if __name__ == "__main__":
    unittest.main()
