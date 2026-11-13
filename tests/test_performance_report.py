"""Phase A safety and reproducibility contracts; no FreeCAD runtime needed."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from smbs_core.kinematics2d.developer_debug_log import (
    summarize_native_trajectory,
)
from smbs_core.kinematics2d.native_kernel_metrics import (
    count_native_kernel_calls, measured_common, measured_distance,
)
from smbs_core.kinematics2d.performance_report import (
    JOURNAL_SCHEMA, analyze_journal, analyze_journal_file, main, report_text,
)


def ev(kind, seq, **data):
    return {
        "event": kind, "sequence": seq,
        "utc": "2026-11-13T08:00:00.000+00:00",
        "session_id": "S1", "data": data,
    }


def sample_journal():
    return {
        "schema_version": JOURNAL_SCHEMA,
        "events": [
            ev("SMBS_RUNTIME_READY", 1, module_file="/Mod/SMBS/InitGui.py"),
            ev("MANUAL_NATIVE_CHECK_STARTED", 2, bend_order=["B07", "B03"],
               punch_id="AP1017", die_id="AT3015",
               conditional_end_zone_allowed=True, end_zone_length_mm=10.0,
               adaptive_config={"arc_segments": 128}),
            ev("PERF_NATIVE_BEND_KERNEL", 3, bend_id="B07", step_number=1,
               elapsed_s=4.5,
               scope="D6_VALIDATION_EXCLUDES_POST_COLLISION_FORENSICS",
               kernel={
                   "scope": "INSTRUMENTED_NATIVE_COLLISION_CALLS_ONLY",
                   "common_calls": 6, "distance_calls": 4,
                   "common_elapsed_s": 2.0, "distance_elapsed_s": 1.0,
                   "failures": 0,
               }),
            ev("MANUAL_NATIVE_BEND_TRAJECTORY", 4, bend_id="B07",
               status="SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
               native_d6_samples={
                   "bend_id": "B07", "elapsed_ms": 4280.0,
                   "evidence_status": "SAMPLED_INTERVAL_CONVERGED",
                   "total_states": 5,
                   "total_collision_queries": 5,
                   "total_solid_reconstructions": 3,
                   "total_common_queries": 6,
                   "total_distance_queries": 4,
                   "phases": [
                       {
                           "phase": "BENDING", "elapsed_ms": 3500.0,
                           "evidence_status": "SAMPLED_INTERVAL_CONVERGED",
                           "collision_queries": 4,
                           "actual_common_queries": 5,
                           "solid_reconstructions": 3,
                           "samples": [{}, {}, {}, {}],
                       },
                       {
                           "phase": "BOTTOM", "elapsed_ms": 200.0,
                           "evidence_status": "SAMPLED_INTERVAL_CONVERGED",
                           "collision_queries": 1,
                           "actual_common_queries": 1,
                           "solid_reconstructions": 0,
                           "samples": [{}],
                       },
                   ],
               }),
            ev("MANUAL_NATIVE_BEND_RESULT", 5, bend={
                "bend_id": "B07", "step_number": 1,
                "status": "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
                "reason": "all sampled clear", "elapsed_s": 4.7,
            }),
            ev("MANUAL_NATIVE_CHECK_FINISHED", 6,
               status="UNVERIFIED", reason="remaining_bends",
               requested_bends=["B07", "B03"],
               checked_bends=1, elapsed_s=5.2),
            ev("MANUAL_NATIVE_CHECK_STARTED", 7,
               bend_order=["B03"], punch_id="AP1017",
               die_id="AT3015", conditional_end_zone_allowed=False),
            ev("MANUAL_NATIVE_BEND_RESULT", 8, bend={
                "bend_id": "B03", "step_number": 1,
                "status": "COLLISION", "reason": "punch",
                "elapsed_s": 1.25,
            }),
            # A crashed/incomplete run must remain completed=False.
        ],
    }


class PerformanceReportTests(unittest.TestCase):
    def test_two_runs_one_interrupted_and_honest_kernel_counts(self):
        report = analyze_journal(sample_journal(), source="SMBS_debug_log.json")
        self.assertEqual(report["run_count"], 2)
        self.assertEqual(report["source_event_count"], 8)
        finished, interrupted = report["runs"]
        self.assertTrue(finished["completed"])
        self.assertFalse(interrupted["completed"])
        self.assertEqual(finished["final_status"], "UNVERIFIED")
        self.assertIsNone(interrupted["final_status"])
        self.assertEqual(finished["runtime"]["module_file"],
                         "/Mod/SMBS/InitGui.py")
        self.assertEqual(finished["requested_order"], ["B07", "B03"])
        self.assertEqual(finished["configuration"]["end_zone_length_mm"], 10.)
        self.assertEqual(finished["totals"]["kernel_common_calls"], 6)
        self.assertEqual(finished["totals"]["kernel_common_elapsed_s"], 2.)
        self.assertEqual(finished["totals"]["total_common_queries"], 6)
        self.assertEqual(finished["totals"]["total_solid_reconstructions"], 3)
        self.assertEqual(finished["bends"][0]["native_d6"]["total_states"], 5)
        self.assertEqual(finished["bends"][0]["phases"][0]["sample_count"], 4)
        self.assertEqual(finished["bends"][0]["kernel_scope"],
                         "D6_VALIDATION_EXCLUDES_POST_COLLISION_FORENSICS")
        self.assertEqual(interrupted["totals"]["observed_bend_count"], 1)
        self.assertIsNone(interrupted["totals"]["kernel_common_calls"])
        self.assertIsNone(interrupted["totals"]["total_solid_reconstructions"])
        self.assertIn("NOT_RECORDED", report_text(report))

    def test_older_log_never_converts_missing_counter_to_zero(self):
        journal = {
            "schema_version": JOURNAL_SCHEMA,
            "events": [
                ev("MANUAL_NATIVE_CHECK_STARTED", 1, bend_order=["B08"]),
                ev("MANUAL_NATIVE_BEND_TRAJECTORY", 2,
                   bend_id="B08", status="COLLISION",
                   native_d6_samples={"bend_id": "B08", "phases": [
                       {"phase": "BENDING", "samples": []},
                   ]}),
                ev("MANUAL_NATIVE_CHECK_FINISHED", 3,
                   status="COLLISION", elapsed_s=3.2),
            ],
        }
        run = analyze_journal(journal)["runs"][0]
        self.assertIsNone(run["bends"][0]["native_d6"]["total_common_queries"])
        self.assertIsNone(run["bends"][0]["phases"][0]["elapsed_s"])
        self.assertIsNone(run["totals"]["kernel_common_calls"])
        self.assertIsNone(run["totals"]["total_common_queries"])

    def test_orphan_events_fail_to_complete_without_finished_event(self):
        result = analyze_journal({
            "schema_version": JOURNAL_SCHEMA,
            "events": [ev("PERF_NATIVE_BEND_KERNEL", 1,
                          bend_id="B05", kernel={"common_calls": 1})],
        })
        self.assertEqual(result["run_count"], 1)
        run = result["runs"][0]
        self.assertEqual(run["origin"], "MISSING_START_EVENT")
        self.assertFalse(run["completed"])
        self.assertIsNone(run["start_utc"])

    def test_rejects_untrusted_schema_and_invalid_events(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            analyze_journal({"schema_version": "not_smbs", "events": []})
        with self.assertRaisesRegex(ValueError, "events must be a list"):
            analyze_journal({"schema_version": JOURNAL_SCHEMA,
                             "events": {}})
        self.assertEqual(
            analyze_journal({"schema_version": JOURNAL_SCHEMA, "events": []})
            ["run_count"], 0,
        )

    def test_cli_and_library_do_not_modify_original_log(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "SMBS_debug_log.json"
            source.write_text(json.dumps(sample_journal()),
                              encoding="utf-8")
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = analyze_journal_file(source)
            self.assertEqual(result["run_count"], 2)
            destination = Path(temp) / "perf_summary.json"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([str(source), "--json", "--output",
                                       str(destination)]), 0)
            parsed = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(parsed["run_count"], 2)
            self.assertEqual(json.loads(output.getvalue())["run_count"], 2)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main([str(source), "--output", str(source)])
            after = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(before, after)


class NativeMetricsSerializationTests(unittest.TestCase):
    def test_summary_reads_existing_counters_no_new_occ_calls(self):
        phase = SimpleNamespace(
            phase_name="BENDING",
            evidence_status="SAMPLED_INTERVAL_CONVERGED",
            elapsed_ms=1260., states=(),
            collision_queries_count=10, solid_reconstructions_count=4,
            actual_common_queries=6, actual_distance_queries=3,
            total_punch_eval_ms=550., total_die_eval_ms=210.,
            phase_solid_reconstruction_ms=420.,
            pose_cache_hits=2, punch_cache_hits=3, die_cache_hits=4,
            solid_cache_hits=5, diagnostics=(),
            unresolved_intervals=(), uninspected_intervals=(),
            discontinuities=(),
        )
        trajectory = SimpleNamespace(
            bend_id="B07", evidence_status="SAMPLED_INTERVAL_CONVERGED",
            continuous_certification_status="NOT_CONTINUOUSLY_CERTIFIED",
            elapsed_ms=1500., phase_results=[phase],
            total_states_evaluated=12, total_solid_reconstructions=4,
            total_pair_collision_checks=9, total_common_queries=6,
            total_distance_queries=3, total_pose_cache_hits=2,
        )
        summary = summarize_native_trajectory(trajectory)
        self.assertEqual(summary["total_solid_reconstructions"], 4)
        self.assertEqual(summary["total_common_queries"], 6)
        self.assertEqual(summary["total_pair_collision_checks"], 9)
        ph = summary["phases"][0]
        self.assertEqual(ph["solid_reconstructions"], 4)
        self.assertEqual(ph["actual_common_queries"], 6)
        self.assertEqual(ph["solid_reconstruction_ms"], 420.)
        self.assertEqual(ph["pose_cache_hits"], 2)
        self.assertEqual(ph["samples"], [])


class NativeKernelCounterTests(unittest.TestCase):
    def test_records_only_instrumented_common_and_distance(self):
        solid = SimpleNamespace(
            common=lambda other: "common",
            distToShape=lambda other: (1.2, None, None),
        )
        with count_native_kernel_calls() as counts:
            self.assertEqual(measured_common(solid, object(), label="punch"),
                             "common")
            self.assertEqual(measured_distance(solid, object(), label="die")[0],
                             1.2)
        self.assertEqual(counts.common_calls, 1)
        self.assertEqual(counts.distance_calls, 1)
        self.assertEqual(counts.labels, {"punch": 1, "die": 1})
        self.assertGreaterEqual(counts.common_elapsed_s, 0.0)
        self.assertGreaterEqual(counts.distance_elapsed_s, 0.0)


if __name__ == "__main__":
    unittest.main()
