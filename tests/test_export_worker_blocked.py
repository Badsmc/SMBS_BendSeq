"""Commit 4B — ExportWorker unit-level tests (§7.3 contract).

Original pipeline-level test test_corrupted_sequence_export_blocked failed
after KL-16 (no machine_frame): initial export from real pipeline is always
BLOCKED (verification=FAILED). §7.3 contract "export MUST BLOCK when
verification failed" was never verified — the test failed on setup.

These tests construct the three input artifacts directly in isolation
(Variant B: self-contained inline fixtures, zero external dependencies),
and call ExportWorker.execute() twice:
1. Valid VERIFIED setup → EXPORTED (proves setup is not trivially blocked)
2. VERIFICATION_FAILED → BLOCKED (contract §7.3)
"""
import sys, unittest, tempfile, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.protocols.file_store import FileStore
from smbs_core.workers.export.worker import ExportWorker


class TestExportWorkerBlocked(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.file_store = FileStore()
        self.export_worker = ExportWorker(file_store=self.file_store)

    def _write_valid_artifacts(self, run_dir: Path) -> tuple[Path, Path, Path]:
        """Write canonical_part, search_result, verification_result in run_dir."""
        part_id = "fixture_l"
        run_id = run_dir.name
        bend_id = "BEND_01_71ef4041"

        canonical_part = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": "cff930fe56b9dcd2a601b48854bec697f612509f46330115e2969a2435966456",
            "producer": {"name": "topology_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-12T03:40:43.170042+00:00",
            "content_hash": "0" * 64,
            "payload": {
                "input_mode": "CANONICAL_PART",
                "thickness_mm": {
                    "confidence": "CONFIRMED",
                    "source": "SHEETMETAL_NATIVE",
                    "unit": "mm",
                    "value": 1.5
                },
                "reference_panel_id": "PANEL_8fde147d",
                "panels": {
                    "PANEL_8fde147d": {
                        "adjacent_bend_ids": [bend_id],
                        "area_mm2": 9550.0,
                        "centroid": [47.75, 25.0, 0.75],
                        "normal": [0.0, -0.0, 1.0],
                        "panel_id": "PANEL_8fde147d"
                    },
                    "PANEL_f7b20067": {
                        "adjacent_bend_ids": [bend_id],
                        "area_mm2": 4750.0,
                        "centroid": [97.25, 25.0, 26.25],
                        "normal": [1.0, 0.0, 0.0],
                        "panel_id": "PANEL_f7b20067"
                    }
                },
                "bends": {
                    bend_id: {
                        "angle_deg": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": -90.0
                        },
                        "axis_direction": [0.0, 1.0, 0.0],
                        "axis_point": [95.5, 0.0, 2.5],
                        "bend_id": bend_id,
                        "child_panel_id": "PANEL_f7b20067",
                        "direction": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": "DOWN"
                        },
                        "k_factor": {
                            "confidence": "CONFIRMED",
                            "source": "SHEETMETAL_NATIVE",
                            "value": 0.44
                        },
                        "length_mm": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": 12.5
                        },
                        "parent_panel_id": "PANEL_8fde147d",
                        "radius_mm": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": 1.0
                        }
                    }
                },
                "geometry_artifact": {
                    "content_hash": "c5460e34199b9e6c4cc66c7282c4f4932cd6f612b4838992b429ec849160bdea",
                    "file_path": f"{run_dir}/geometry.brep",
                    "format": "BREP"
                }
            }
        }

        search_result = {
            "schema_name": "search_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": "cff930fe56b9dcd2a601b48854bec697f612509f46330115e2969a2435966456",
            "producer": {"name": "solver_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-12T03:40:43.883035+00:00",
            "content_hash": "0" * 64,
            "payload": {
                "planning_status": "SUCCESS",
                "termination_reason": "GOAL_REACHED",
                "optimality_claim": "best_found_under_budget",
                "total_cost": 1.0,
                "solver_metrics": {
                    "exact_collision_queries": 1,
                    "peak_memory_mb": 15.5,
                    "prefilter_checks": 0,
                    "solver_nodes": 2,
                    "trajectory_samples": 5,
                    "wall_time_s": 0.0127
                },
                "candidate_sequence": [
                    {
                        "step": 1,
                        "bend_id": bend_id,
                        "direction": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": "DOWN"
                        },
                        "angle_deg": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": -90.0
                        },
                        "radius_mm": {
                            "confidence": "CONFIRMED",
                            "source": "GEOMETRIC_INFERENCE",
                            "value": 1.0
                        },
                        "k_factor": {
                            "confidence": "CONFIRMED",
                            "source": "SHEETMETAL_NATIVE",
                            "value": 0.44
                        },
                        "backgauge": {
                            "confidence": "ESTIMATED",
                            "r_mm": 0.0,
                            "x_mm": 20.0,
                            "z1_mm": -25.0,
                            "z2_mm": 25.0
                        },
                        "tool_id": "PUNCH_STD_R1",
                        "planner_status": "VALID",
                        "collision_status": "VALID",
                        "tooling_status": "SIMPLIFIED",
                        "alignment_status": "OK"
                    }
                ]
            }
        }

        verification_result = {
            "schema_name": "verification_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": "cff930fe56b9dcd2a601b48854bec697f612509f46330115e2969a2435966456",
            "producer": {"name": "verifier_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": "SUCCESS",
            "diagnostics": [],
            "created_at": "2026-10-12T03:40:44.136561+00:00",
            "content_hash": "0" * 64,
            "payload": {
                "verification_status": "VERIFIED",
                "verification_independence_level": "ISOLATED_OCC_NO_SOLVER_CACHE",
                "production_eligible": True,
                "independent_metrics": {
                    "bbox_delta_mm": [0.0, 0.0, 0.0],
                    "hausdorff_distance_mm": 0.001,
                    "is_valid_brep": True,
                    "relative_volume_delta": 0.0,
                    "shells_count": 1,
                    "solids_count": 1,
                    "surface_area_delta_mm2": 0.0,
                    "volume_delta_mm3": 0.0
                },
                "step_verifications": [
                    {
                        "step": 1,
                        "bend_id": bend_id,
                        "status": "VERIFIED",
                        "collision_verified": True,
                        "kinematics_verified": True
                    }
                ]
            }
        }

        part_file = run_dir / "04_canonical_part.json"
        search_file = run_dir / "07_search_result.json"
        verif_file = run_dir / "09_verification_result.json"

        self.file_store.write_json_atomic(part_file, canonical_part, schema_name="canonical_part")
        self.file_store.write_json_atomic(search_file, search_result, schema_name="search_result")
        self.file_store.write_json_atomic(verif_file, verification_result, schema_name="verification_result")

        return search_file, verif_file, part_file

    def test_export_exported_when_verified(self):
        """Sanity: valid VERIFIED setup → EXPORTED, 10_part_bendseq.json produced."""
        run_dir = self.tmp / "run_verified"
        run_dir.mkdir(parents=True, exist_ok=True)
        search_path, verif_path, part_path = self._write_valid_artifacts(run_dir)

        out_file, report_file = self.export_worker.execute(search_path, verif_path, part_path)
        self.assertTrue(
            (run_dir / "10_part_bendseq.json").exists(),
            "VERIFIED setup must produce 10_part_bendseq.json"
        )
        self.assertFalse(
            (run_dir / "10_part_bendseq_candidate.json").exists(),
            "VERIFIED setup must not produce 10_part_bendseq_candidate.json"
        )
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("export_status"), "EXPORTED")
        self.assertTrue(data.get("production_eligible"))

    def test_export_blocked_when_verification_failed(self):
        """Contract §7.3: corrupt verification → BLOCKED, no final export."""
        run_dir = self.tmp / "run_failed"
        run_dir.mkdir(parents=True, exist_ok=True)
        search_path, verif_path, part_path = self._write_valid_artifacts(run_dir)

        verif_data = json.loads(verif_path.read_text(encoding="utf-8"))
        verif_data["payload"]["verification_status"] = "VERIFICATION_FAILED"
        verif_data["payload"]["production_eligible"] = False
        verif_data["status"] = "VERIFICATION_FAILED"
        self.file_store.write_json_atomic(verif_path, verif_data, schema_name="verification_result")

        out_file, report_file = self.export_worker.execute(search_path, verif_path, part_path)
        self.assertFalse(
            (run_dir / "10_part_bendseq.json").exists(),
            "VERIFICATION_FAILED must not produce 10_part_bendseq.json (invariant #2)"
        )
        self.assertTrue(
            (run_dir / "10_part_bendseq_candidate.json").exists(),
            "BLOCKED state must still produce 10_part_bendseq_candidate.json (honest plumbing)"
        )
        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertFalse(data.get("production_eligible"))
        self.assertEqual(data.get("export_status"), "BLOCKED")
        self.assertEqual(data.get("verification_status"), "VERIFICATION_FAILED")


if __name__ == "__main__":
    unittest.main()
