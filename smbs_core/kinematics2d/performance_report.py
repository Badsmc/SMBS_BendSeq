"""Read-only analysis of the existing SMBS Desktop developer JSON journal.

Phase A instrumentation only. This module never imports FreeCAD, changes
CAD geometry, measures a new run, or infers native collision clearance.
Missing historical counters are null, never 0 or a guessed value.
Run with: python -m smbs_core.kinematics2d.performance_report <journal.json>
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


REPORT_SCHEMA = "smbs.performance_baseline.v1"
JOURNAL_SCHEMA = "smbs.desktop_developer_debug.v1"
_TOP_METRICS = (
    "total_states", "total_collision_queries",
    "total_solid_reconstructions", "total_pair_collision_checks",
    "total_distance_queries", "total_common_queries",
    "total_pose_cache_hits", "total_punch_cache_hits",
    "total_die_cache_hits", "total_solid_cache_hits",
)
_PHASE_METRICS = (
    "collision_queries", "solid_reconstructions",
    "pair_collision_checks", "actual_distance_queries",
    "actual_common_queries", "pose_cache_hits", "pose_cache_misses",
    "solid_cache_hits", "solid_cache_misses",
    "punch_cache_hits", "punch_cache_misses",
    "die_cache_hits", "die_cache_misses",
    "punch_eval_ms", "die_eval_ms",
    "solid_reconstruction_ms", "cache_lookup_ms",
    "max_punch_overlap_mm3", "max_die_overlap_mm3",
)


def _number(value: Any) -> float | None:
    """Return only valid nonnegative, nonboolean finite numeric metrics."""
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def _seconds_from_ms(value: Any) -> float | None:
    v = _number(value)
    return None if v is None else round(v / 1000.0, 6)


def _metric(value: Any) -> int | float | None:
    """Leave integer counters as integers. Do not coerce garbage to zero."""
    v = _number(value)
    if v is None:
        return None
    return int(value) if isinstance(value, int) else v


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _d6_summary(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, Any] = {
        "bend_id": raw.get("bend_id"),
        "evidence_status": raw.get("evidence_status"),
        "continuous_certification_status": raw.get(
            "continuous_certification_status"
        ),
        "elapsed_s": _seconds_from_ms(raw.get("elapsed_ms")),
        "phases": [],
    }
    for key in _TOP_METRICS:
        result[key] = _metric(raw.get(key))
    for source in raw.get("phases") or ():
        if not isinstance(source, dict):
            continue
        samples = source.get("samples")
        phase: dict[str, Any] = {
            "name": source.get("phase"),
            "evidence_status": source.get("evidence_status"),
            "is_safe": source.get("is_safe"),
            "is_conditional": source.get("is_conditional"),
            "elapsed_s": _seconds_from_ms(source.get("elapsed_ms")),
            "sample_count": (
                len(samples) if isinstance(samples, list) else None
            ),
            "unresolved_interval_count": (
                len(source["unresolved_intervals"])
                if isinstance(source.get("unresolved_intervals"), list)
                else None
            ),
            "uninspected_interval_count": (
                len(source["uninspected_intervals"])
                if isinstance(source.get("uninspected_intervals"), list)
                else None
            ),
        }
        for key in _PHASE_METRICS:
            phase[key] = _metric(source.get(key))
        result["phases"].append(phase)
    return result


def _start_run(event: Mapping[str, Any], *, orphan: bool = False,
               runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = _mapping(event.get("data"))
    order = data.get("bend_order")
    return {
        "start_utc": None if orphan else event.get("utc"),
        "finish_utc": None,
        "start_sequence": None if orphan else event.get("sequence"),
        "finish_sequence": None,
        "session_id": event.get("session_id"),
        "runtime": dict(runtime) if runtime else None,
        "origin": "MISSING_START_EVENT" if orphan else "MANUAL_NATIVE_CHECK_STARTED",
        "final_status": None,
        "final_reason": None,
        "completed": False,
        "requested_order": (
            list(order) if isinstance(order, (tuple, list)) else []
        ),
        "configuration": {
            "punch_id": data.get("punch_id"),
            "die_id": data.get("die_id"),
            "punch_file": data.get("punch_file"),
            "die_file": data.get("die_file"),
            "virtual_tool_length": data.get("virtual_tool_length"),
            "tool_sections": data.get("tool_sections"),
            "conditional_end_zone_allowed": data.get(
                "conditional_end_zone_allowed"
            ),
            "end_zone_length_mm": data.get("end_zone_length_mm"),
            "effective_end_zone_policy": data.get(
                "effective_end_zone_policy"
            ),
            "adaptive_config": data.get("adaptive_config"),
        },
        "elapsed_s": None,
        "bends": [],
    }


def _bend(run: dict[str, Any], bend_id: str) -> dict[str, Any]:
    for item in run["bends"]:
        if item["bend_id"] == bend_id:
            return item
    item = {
        "bend_id": bend_id,
        "step_number": None,
        "status": None,
        "reason": None,
        "elapsed_s": None,
        "native_d6": None,
        "phases": [],
        "kernel": None,
        "geometry_build": None,
        "component_shadow": None,
        "shared_geometry_cache": None,
        "loaded_native_modules": None,
        "kernel_scope": None,
        "kernel_validation_elapsed_s": None,
        "first_blocker": None,
    }
    run["bends"].append(item)
    return item


def _sum_complete(observations: list[Any]) -> int | float | None:
    if not observations or any(v is None for v in observations):
        return None
    return sum(observations)


def _totals(run: dict[str, Any]) -> dict[str, Any]:
    bends = run["bends"]
    result: dict[str, Any] = {
        "observed_bend_count": len(bends),
        "native_d6_bend_count": sum(
            isinstance(x["native_d6"], dict) for x in bends
        ),
        "bend_elapsed_s_sum": _sum_complete(
            [x["elapsed_s"] for x in bends]
        ),
        "native_d6_elapsed_s_sum": _sum_complete([
            x["native_d6"]["elapsed_s"]
            if x["native_d6"] is not None else None for x in bends
        ]),
    }
    for key in _TOP_METRICS:
        result[key] = _sum_complete([
            x["native_d6"].get(key)
            if x["native_d6"] is not None else None for x in bends
        ])
    for key in (
        "common_calls", "distance_calls", "common_elapsed_s",
        "distance_elapsed_s", "failures",
    ):
        result["kernel_" + key] = _sum_complete([
            x["kernel"].get(key)
            if isinstance(x["kernel"], dict) else None for x in bends
        ])
    # Keep measured OCC union and zone construction separate from any
    # uninstrumented/native time. Missing historical observations are null.
    region_names = (
        "panel_extrude", "panel_transform", "bend_zone_build",
        "sheet_union", "sheet_qualification",
        "machine_workpiece_copy", "machine_workpiece_transform",
        "native_punch_pose", "pair_geometry_sanity",
    )
    result["geometry_build_measured_total_s"] = _sum_complete([
        _mapping(x["geometry_build"]).get("measured_total_s")
        if isinstance(x["geometry_build"], dict) else None
        for x in bends
    ])
    for name in region_names:
        result["geometry_" + name + "_elapsed_s"] = _sum_complete([
            _mapping(_mapping(x["geometry_build"]).get("elapsed_s")).get(name)
            if isinstance(x["geometry_build"], dict) else None
            for x in bends
        ])
    # Shadow observations are deliberately advisory; they NEVER add to
    # authoritative native OCC/clearance counts or safety qualifications.
    for name in (
        "probes", "exact_tool_screens", "aabb_rejected",
        "exact_candidates", "transformed_components",
        "component_common_calls", "proven_component_hits",
        "disagreements", "incomplete_probes", "measured_s",
        "component_rebuild_calls", "component_rebuild_s",
    ):
        result["component_shadow_" + name] = _sum_complete([
            _mapping(x["component_shadow"]).get(name)
            if isinstance(x["component_shadow"], dict) else None
            for x in bends
        ])
    for name in (
        "bend_zone_cache_hit", "bend_zone_cache_miss",
        "bend_zone_cache_eviction",
    ):
        result["geometry_" + name] = _sum_complete([
            _mapping(_mapping(x["geometry_build"]).get("cache_events")).get(name)
            if isinstance(x["geometry_build"], dict) else None
            for x in bends
        ])
    return result


def analyze_journal(data: Mapping[str, Any], *,
                    source: str = "<in-memory>") -> dict[str, Any]:
    """Group one or more actual native Manual runs from an event journal."""
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object with events")
    if data.get("schema_version") != JOURNAL_SCHEMA:
        raise ValueError("Unsupported SMBS Desktop developer journal schema")
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("SMBS journal events must be a list")

    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_runtime: Mapping[str, Any] | None = None

    for event in events:
        if not isinstance(event, dict):
            continue
        name = event.get("event")
        payload = _mapping(event.get("data"))
        if name == "SMBS_RUNTIME_READY":
            last_runtime = payload
            continue
        if name == "MANUAL_NATIVE_CHECK_STARTED":
            # An interrupted prior run is kept, not falsely completed.
            if current is not None:
                current["totals"] = _totals(current)
                runs.append(current)
            current = _start_run(event, runtime=last_runtime)
            continue

        interesting = {
            "MANUAL_NATIVE_BEND_TRAJECTORY",
            "PERF_NATIVE_BEND_KERNEL",
            "MANUAL_NATIVE_BEND_RESULT",
            "MANUAL_NATIVE_CHECK_FINISHED",
        }
        if name not in interesting:
            continue
        if current is None:
            current = _start_run(event, orphan=True, runtime=last_runtime)

        if name == "MANUAL_NATIVE_BEND_TRAJECTORY":
            bend_id = payload.get("bend_id")
            if not isinstance(bend_id, str) or not bend_id:
                continue
            record = _bend(current, bend_id)
            d6 = _d6_summary(payload.get("native_d6_samples"))
            record["native_d6"] = d6
            record["phases"] = d6["phases"] if d6 else []
            if record["status"] is None:
                record["status"] = payload.get("status")
            if record["reason"] is None:
                record["reason"] = payload.get("reason")
        elif name == "PERF_NATIVE_BEND_KERNEL":
            bend_id = payload.get("bend_id")
            if not isinstance(bend_id, str) or not bend_id:
                continue
            record = _bend(current, bend_id)
            record["kernel"] = (
                dict(payload["kernel"])
                if isinstance(payload.get("kernel"), dict) else None
            )
            record["geometry_build"] = (
                dict(payload["geometry_build"])
                if isinstance(payload.get("geometry_build"), dict)
                else None
            )
            record["component_shadow"] = (
                dict(payload["component_shadow"])
                if isinstance(payload.get("component_shadow"), dict)
                else None
            )
            record["shared_geometry_cache"] = (
                dict(payload["shared_geometry_cache"])
                if isinstance(payload.get("shared_geometry_cache"), dict)
                else None
            )
            record["loaded_native_modules"] = (
                dict(payload["loaded_native_modules"])
                if isinstance(payload.get("loaded_native_modules"), dict)
                else None
            )
            record["kernel_scope"] = payload.get("scope")
            record["kernel_validation_elapsed_s"] = _number(
                payload.get("elapsed_s")
            )
            if isinstance(payload.get("step_number"), int):
                record["step_number"] = payload["step_number"]
        elif name == "MANUAL_NATIVE_BEND_RESULT":
            item = _mapping(payload.get("bend"))
            bend_id = item.get("bend_id")
            if not isinstance(bend_id, str) or not bend_id:
                continue
            record = _bend(current, bend_id)
            record["step_number"] = item.get("step_number")
            record["status"] = item.get("status")
            record["reason"] = item.get("reason")
            record["elapsed_s"] = _number(item.get("elapsed_s"))
            record["first_blocker"] = (
                dict(item["first_blocker"])
                if isinstance(item.get("first_blocker"), dict) else None
            )
        elif name == "MANUAL_NATIVE_CHECK_FINISHED":
            current["completed"] = True
            current["finish_utc"] = event.get("utc")
            current["finish_sequence"] = event.get("sequence")
            current["final_status"] = payload.get("status")
            current["final_reason"] = payload.get("reason")
            current["elapsed_s"] = _number(payload.get("elapsed_s"))
            if not current["requested_order"]:
                order = payload.get("requested_bends")
                if isinstance(order, list):
                    current["requested_order"] = list(order)
            current["totals"] = _totals(current)
            runs.append(current)
            current = None

    if current is not None:
        current["totals"] = _totals(current)
        runs.append(current)
    return {
        "schema_version": REPORT_SCHEMA,
        "source_journal": source,
        "source_journal_schema": JOURNAL_SCHEMA,
        "source_event_count": len(events),
        "run_count": len(runs),
        "runs": runs,
        "scope": (
            "RECORDED_MANUAL_NATIVE_D6_ONLY; "
            "NOT_A_CERTIFICATE_OF_CONTINUOUS_COLLISION_CLEARANCE"
        ),
    }


def analyze_journal_file(path: str | Path) -> dict[str, Any]:
    """Load an existing journal without modifying it or importing FreeCAD."""
    file = Path(path).expanduser()
    with file.open("r", encoding="utf-8") as stream:
        parsed = json.load(stream)
    return analyze_journal(parsed, source=file.name)


def compare_native_reports(baseline: Mapping[str, Any],
                           candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Compare latest completed manual runs, fail closed on mismatched evidence.

    This is a *measurement aid*, not a substitute for native FreeCAD
    acceptance. Runtime FreeCAD version/sha and input file hashes must still
    be independently checked: an old Desktop JSON may omit these.
    """
    if (baseline.get("schema_version") != REPORT_SCHEMA
            or candidate.get("schema_version") != REPORT_SCHEMA):
        raise ValueError("Expected two SMBS performance reports")

    def last_completed(report):
        completed = [
            run for run in report.get("runs", ())
            if run.get("completed") is True
        ]
        return completed[-1] if completed else None

    left, right = last_completed(baseline), last_completed(candidate)
    issues: list[str] = []
    if left is None or right is None:
        issues.append("missing_complete_run_in_one_or_both_journals")
    if left is not None and right is not None:
        if left.get("requested_order") != right.get("requested_order"):
            issues.append("requested_bend_order_mismatch")
        if left.get("configuration") != right.get("configuration"):
            issues.append("tooling_or_sampling_configuration_mismatch")
        if left.get("final_status") != right.get("final_status"):
            issues.append("overall_native_status_mismatch")
        a = left.get("bends") or []
        b = right.get("bends") or []
        if len(a) != len(b):
            issues.append("different_number_of_checked_bends")
        for step, (x, y) in enumerate(zip(a, b), 1):
            if x.get("bend_id") != y.get("bend_id"):
                issues.append("bend_id_mismatch_at_step_{}".format(step))
                continue
            if x.get("status") != y.get("status"):
                issues.append("physical_verdict_mismatch_at_step_{}:{}".format(
                    step, x.get("bend_id")
                ))
            xd = x.get("native_d6") or {}
            yd = y.get("native_d6") or {}
            if xd.get("evidence_status") != yd.get("evidence_status"):
                issues.append("native_d6_evidence_mismatch_at_step_{}:{}".format(
                    step, x.get("bend_id")
                ))
            xx = x.get("first_blocker") or {}
            yy = y.get("first_blocker") or {}
            for key in ("tool", "phase"):
                if xx.get(key) != yy.get(key):
                    issues.append("collision_{}_mismatch_at_step_{}:{}".format(
                        key, step, x.get("bend_id")
                    ))
            if xx.get("angle_deg") is not None or yy.get("angle_deg") is not None:
                def signed_angle(blocker):
                    val = blocker.get("angle_deg")
                    if isinstance(val, bool) or not isinstance(val, (float, int)):
                        return None
                    num = float(val)
                    return num if math.isfinite(num) else None

                xv = signed_angle(xx)
                yv = signed_angle(yy)
                if xv is None or yv is None or abs(xv - yv) > 1e-4:
                    issues.append("first_collision_angle_mismatch_at_step_{}:{}"
                                  .format(step, x.get("bend_id")))

    elapsed_left = (
        _number(left.get("elapsed_s")) if left is not None else None
    )
    elapsed_right = (
        _number(right.get("elapsed_s")) if right is not None else None
    )
    ratio = (
        round(elapsed_left / elapsed_right, 4)
        if not issues and elapsed_left is not None
        and elapsed_right is not None and elapsed_right > 0
        else None
    )
    return {
        "schema_version": "smbs.performance_comparison.v1",
        "baseline_source": baseline.get("source_journal"),
        "candidate_source": candidate.get("source_journal"),
        "matched_recorded_configuration_and_verdicts": not issues,
        "issues": issues,
        "elapsed_baseline_s": elapsed_left,
        "elapsed_candidate_s": elapsed_right,
        "observed_wall_speedup_x": ratio,
        "native_freecad_input_hashes_verified": False,
        "independent_native_collision_regression_qualified": False,
        "scope": (
            "LOG_LEVEL_COMPARISON_ONLY; matched input FCStd/tool SHA-256 "
            "and native FreeCAD physical acceptance REQUIRED"
        ),
    }


def _display(value: Any) -> str:
    if value is None:
        return "NOT_RECORDED"
    if isinstance(value, float):
        return "{:.3f}".format(value)
    return str(value)


def report_text(report: Mapping[str, Any]) -> str:
    lines = [
        "SMBS native 3D performance report (read-only)",
        "Source: {}".format(report["source_journal"]),
        "Native manual runs: {}".format(report["run_count"]),
        "CAUTION: sampled evidence only, not continuous certification",
    ]
    for n, run in enumerate(report["runs"], 1):
        totals = run["totals"]
        lines.append(
            "Run {} | {} | complete={} | elapsed={} s | recorded bends={}"
            .format(n, _display(run["final_status"]), run["completed"],
                    _display(run["elapsed_s"]), len(run["bends"]))
        )
        lines.append(
            "  OCC common={} ({} s), distances={} ({} s), "
            "full solid rebuilds={}".format(
                _display(totals["kernel_common_calls"]),
                _display(totals["kernel_common_elapsed_s"]),
                _display(totals["kernel_distance_calls"]),
                _display(totals["kernel_distance_elapsed_s"]),
                _display(totals["total_solid_reconstructions"]),
            )
        )
        lines.append(
            "  Native sheet build: union={} s, bend zones={} s, "
            "panel transform={} s, zone cache hits={}"
            .format(
                _display(totals["geometry_sheet_union_elapsed_s"]),
                _display(totals["geometry_bend_zone_build_elapsed_s"]),
                _display(totals["geometry_panel_transform_elapsed_s"]),
                _display(totals["geometry_bend_zone_cache_hit"]),
            )
        )
        for bend in run["bends"]:
            lines.append(
                "  {}: {} | wall={} s | native={} s | phase samples={}"
                .format(
                    bend["bend_id"], _display(bend["status"]),
                    _display(bend["elapsed_s"]),
                    _display(
                        bend["native_d6"]["elapsed_s"]
                        if bend["native_d6"] else None
                    ),
                    ",".join(
                        "{}:{}".format(p["name"], _display(p["sample_count"]))
                        for p in bend["phases"]
                    ) or "NOT_RECORDED",
                )
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only performance summary for SMBS_debug_log.json"
    )
    parser.add_argument("journal", type=Path, help="Existing Desktop journal")
    parser.add_argument(
        "--json", action="store_true", help="Print structured JSON to stdout"
    )
    parser.add_argument(
        "--output", type=Path, help="Optionally write a separate JSON report"
    )
    parser.add_argument(
        "--compare", type=Path,
        help="Compare with a second untouched Desktop journal; "
             "refuse speedup ratio on mismatched native verdicts"
    )
    args = parser.parse_args(argv)
    source = args.journal.expanduser()
    if args.output is not None:
        output = args.output.expanduser()
        protected = [source.resolve()]
        if args.compare is not None:
            protected.append(args.compare.expanduser().resolve())
        if output.resolve() in protected:
            parser.error("Output must NOT overwrite a source journal")
    summary = analyze_journal_file(source)
    if args.compare is not None:
        summary = compare_native_reports(
            summary, analyze_journal_file(args.compare.expanduser())
        )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.output.expanduser().write_text(
            rendered + "\n", encoding="utf-8"
        )
    if args.compare is not None:
        print(rendered if args.json else (
            "SMBS native comparison: {}\nBaseline: {} s; candidate: {} s; "
            "recorded speedup: {}x\nMismatches: {}\n"
            "CAUTION: input hashes and native FreeCAD qualification remain unverified"
        ).format(
            "MATCHED" if summary["matched_recorded_configuration_and_verdicts"]
            else "NOT COMPARABLE",
            _display(summary["elapsed_baseline_s"]),
            _display(summary["elapsed_candidate_s"]),
            _display(summary["observed_wall_speedup_x"]),
            ", ".join(summary["issues"]) or "none",
        ))
    else:
        print(rendered if args.json else report_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
