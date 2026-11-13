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
from tools.run_native_benchmark import MANUAL_ORDER, selected_bend_prefix
from smbs_core.kinematics2d.native_kernel_metrics import (
    count_native_kernel_calls, measured_common, measured_distance,
)
from smbs_core.kinematics2d.performance_report import (
    JOURNAL_SCHEMA, analyze_journal, analyze_journal_file, main, report_text,
    compare_native_reports,
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
               shared_geometry_cache={"full_sheet_entries": 4,
                                      "source_resets": 1,
                                      "full_sheet_limit": 16},
               loaded_native_modules={
                   "smbs_core.kinematics2d.native_collision_3d": {
                       "path": "C:/FreeCAD/Mod/SMBS/native_collision_3d.py",
                       "sha256": "abc",
                   }
               },
               kernel={
                   "scope": "INSTRUMENTED_NATIVE_COLLISION_CALLS_ONLY",
                   "common_calls": 6, "distance_calls": 4,
                   "common_elapsed_s": 2.0, "distance_elapsed_s": 1.0,
                   "failures": 0,
               },
               geometry_build={
                   "scope": "INSTRUMENTED_NATIVE_SHEET_CONSTRUCTION_ONLY",
                   "measured_total_s": 1.2,
                   "elapsed_s": {
                       "panel_extrude": 0.1, "panel_transform": 0.2,
                       "bend_zone_build": 0.3, "sheet_union": 0.5,
                       "sheet_qualification": 0.1,
                   },
                   "cache_events": {
                       "bend_zone_cache_hit": 42,
                       "bend_zone_cache_miss": 9,
                       "bend_zone_cache_eviction": 0,
                   },
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
    def test_real_benchmark_prefix_selection_never_claims_full_order(self):
        self.assertEqual(selected_bend_prefix(), MANUAL_ORDER)
        self.assertEqual(selected_bend_prefix(1), ("B07",))
        self.assertEqual(selected_bend_prefix(2), ("B07", "B03"))
        self.assertEqual(selected_bend_prefix(10), MANUAL_ORDER)
        for invalid in (0, 11, -1, 1.0, True, "1", None if False else []):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    selected_bend_prefix(invalid)

    def test_machine_pose_reuse_counts_from_native_d6_remain_distinct(self):
        sample = sample_journal()
        d6 = sample["events"][3]["data"]["native_d6_samples"]
        d6["total_machine_pose_reuse_hits"] = 7
        d6["total_machine_pose_reuse_misses"] = 5
        report = analyze_journal(sample)
        good = report["runs"][0]
        self.assertEqual(
            good["bends"][0]["native_d6"]["total_machine_pose_reuse_hits"], 7)
        self.assertEqual(good["totals"]["total_machine_pose_reuse_hits"], 7)
        self.assertEqual(good["totals"]["total_machine_pose_reuse_misses"], 5)
        self.assertIsNone(
            report["runs"][1]["totals"]["total_machine_pose_reuse_hits"])

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
        self.assertAlmostEqual(
            finished["totals"]["geometry_sheet_union_elapsed_s"], 0.5,
        )
        self.assertAlmostEqual(
            finished["totals"]["geometry_bend_zone_build_elapsed_s"], 0.3,
        )
        self.assertEqual(finished["totals"]["geometry_bend_zone_cache_hit"], 42)
        self.assertEqual(finished["totals"]["total_common_queries"], 6)
        self.assertEqual(finished["totals"]["total_solid_reconstructions"], 3)
        self.assertEqual(finished["bends"][0]["native_d6"]["total_states"], 5)
        self.assertEqual(finished["bends"][0]["phases"][0]["sample_count"], 4)
        self.assertEqual(finished["bends"][0]["kernel_scope"],
                         "D6_VALIDATION_EXCLUDES_POST_COLLISION_FORENSICS")
        self.assertEqual(
            finished["bends"][0]["shared_geometry_cache"]["full_sheet_entries"],
            4,
        )
        self.assertEqual(
            finished["bends"][0]["shared_geometry_cache"]["full_sheet_limit"],
            16,
        )
        self.assertEqual(
            finished["bends"][0]["loaded_native_modules"][
                "smbs_core.kinematics2d.native_collision_3d"
            ]["sha256"],
            "abc",
        )
        self.assertEqual(interrupted["totals"]["observed_bend_count"], 1)
        self.assertIsNone(interrupted["totals"]["kernel_common_calls"])
        self.assertIsNone(
            interrupted["totals"]["geometry_sheet_union_elapsed_s"]
        )
        self.assertIsNone(interrupted["totals"]["total_solid_reconstructions"])
        self.assertIn("NOT_RECORDED", report_text(report))

    def test_advisory_shadow_metrics_are_reported_but_not_merged_into_oracle_counts(self):
        sample = sample_journal()
        sample["events"][2]["data"]["component_shadow"] = {
            "scope": "COMPONENT_SHADOW_ADVISORY_ONLY",
            "probes": 2,
            "exact_tool_screens": 4,
            "aabb_rejected": 70,
            "exact_candidates": 14,
            "transformed_components": 3,
            "component_common_calls": 3,
            "proven_component_hits": 1,
            "disagreements": 0,
            "incomplete_probes": 2,
            "measured_s": 0.153,
        }
        report = analyze_journal(sample)
        first = report["runs"][0]
        self.assertEqual(first["totals"]["component_shadow_probes"], 2)
        self.assertEqual(first["totals"]["component_shadow_aabb_rejected"], 70)
        self.assertEqual(first["totals"]["component_shadow_component_common_calls"], 3)
        self.assertEqual(first["totals"]["kernel_common_calls"], 6)
        self.assertEqual(first["totals"]["total_common_queries"], 6)
        self.assertIsNone(
            report["runs"][1]["totals"]["component_shadow_probes"]
        )

    def test_compare_same_report_allows_only_provisional_speedup(self):
        baseline = analyze_journal(sample_journal())
        newer = analyze_journal(sample_journal())
        comparison = compare_native_reports(baseline, newer)
        self.assertTrue(
            comparison["matched_recorded_configuration_and_verdicts"]
        )
        self.assertEqual(comparison["observed_wall_speedup_x"], 1.0)
        self.assertFalse(comparison["native_freecad_input_hashes_verified"])
        self.assertFalse(
            comparison["independent_native_collision_regression_qualified"]
        )

    def test_compare_refuses_any_reported_speedup_for_verdict_regression(self):
        baseline = analyze_journal(sample_journal())
        changed = analyze_journal(sample_journal())
        changed["runs"][0]["bends"][0]["status"] = "COLLISION"
        result = compare_native_reports(baseline, changed)
        self.assertIsNone(result["observed_wall_speedup_x"])
        self.assertFalse(
            result["matched_recorded_configuration_and_verdicts"]
        )
        self.assertTrue(any(
            "physical_verdict_mismatch" in s for s in result["issues"]
        ))

    def test_compare_refuses_sampling_or_tooling_change(self):
        baseline = analyze_journal(sample_journal())
        changed = analyze_journal(sample_journal())
        changed["runs"][0]["configuration"]["end_zone_length_mm"] = 20.0
        result = compare_native_reports(baseline, changed)
        self.assertIsNone(result["observed_wall_speedup_x"])
        self.assertIn(
            "tooling_or_sampling_configuration_mismatch", result["issues"]
        )

    def test_compare_cli_does_not_overwrite_either_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "baseline.json"
            newer = Path(temp) / "candidate.json"
            source.write_text(json.dumps(sample_journal()), encoding="utf-8")
            newer.write_text(json.dumps(sample_journal()), encoding="utf-8")
            old = (source.read_bytes(), newer.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main([str(source), "--compare", str(newer), "--json"]),
                    0,
                )
            self.assertEqual(
                json.loads(output.getvalue())["observed_wall_speedup_x"], 1.0
            )
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main([str(source), "--compare", str(newer),
                          "--output", str(newer)])
            self.assertEqual(old, (source.read_bytes(), newer.read_bytes()))

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
