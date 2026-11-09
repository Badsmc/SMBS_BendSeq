"""tools/benchmark_2d_vs_baseline.py - Unified Benchmark: BASELINE vs 2D-SCREENING.

Performs reproducible comparison of:
- Mode A (BASELINE): Standard 2.5D sequence search without 2D multi-station risk ranking.
- Mode B (2D_SCREENING): Search with opt-in 2D multi-station screening and risk ranking
                         (experimental_multi_station_2d=True, experimental_2d_risk_ranking=True).

Instruments exact OCCT operations using native_kernel_metrics.count_native_kernel_calls()
and exports side-by-side comparison using screening_experiment_metrics.compare_measurements().
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in (
    "/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod",
    "/Applications/FreeCAD.app/Contents/Resources/lib",
):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from SheetMetalUnfoldCmd import SMUnfold
except ImportError as exc:
    if getattr(exc, "name", None) != "SheetMetalUnfoldCmd":
        raise
    from sheetmetal.SheetMetalUnfoldCmd import SMUnfold

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.operation_cost import OperationWeights
from smbs_core.kinematics2d.sequence_search import (
    StepEvaluationCache,
    search_bend_sequence_25d,
    search_bend_sequence_backward_25d,
)
from smbs_core.kinematics2d.native_kernel_metrics import count_native_kernel_calls
from smbs_core.kinematics2d.verified_tool_spans import verified_fixed_tool_spans
from smbs_core.kinematics2d.screening_execution_metrics import count_station_screenings
from hashlib import sha256
from smbs_core.kinematics2d.screening_experiment_metrics import (
    SearchMeasurement,
    compare_measurements,
    from_search_report,
)


def get_git_info() -> Tuple[str, bool]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
        return commit, len(status) > 0
    except Exception:
        return "unknown", False


def run_benchmark_run(
    mode: str,  # 'BASELINE' or '2D_RISK'
    direction: str,  # 'FORWARD' or 'BACKWARD'
    fixture_name: str,
    punch_name: str,
    die_name: str,
    sampling_cfg: AdaptiveSamplingConfig,
    weights: OperationWeights,
    max_search_time_s: float,
    max_states: int,
    max_checks: int,
    match_active_bend_length: bool = False,
    allow_end_zone_collision: bool = False,
    end_zone_length_mm: float = 0.0,
    optimize_cost: bool = False,
    section_offsets_xm: Tuple[float, ...] = (),
) -> Tuple[SearchMeasurement, Dict[str, Any], float]:
    """Execute a single search run in a clean isolated session with cold cache."""
    fixture_path = demo_part_path(fixture_name)
    if not fixture_path or not os.path.exists(str(fixture_path)):
        raise FileNotFoundError(f"Fixture not found: {fixture_name}")

    doc = App.openDocument(str(fixture_path), True)
    try:
        unfold = next((o for o in doc.Objects if o.Name.endswith("_Unfold")), None)
        if unfold is None:
            body = getattr(doc, "Body", None)
            if body is None:
                raise ValueError(f"No Body or _Unfold object in document {fixture_name}")
            unfold = doc.addObject("Part::FeaturePython", "Тело_Unfold")
            root_face = "Face2"
            try:
                SMUnfold(unfold, body, [root_face])
            except Exception:
                root_face = "Face1"
                SMUnfold(unfold, body, [root_face])
            unfold.GenerateSketch = True
            doc.recompute()

        session = build_part_session(doc, [unfold])
        panel = _PreviewPanel()
        panel.session = session
        p_tool = panel.tool_library.load_tool(punch_name, ToolKind.PUNCH)
        d_tool = panel.tool_library.load_tool(die_name, ToolKind.DIE)
        panel.select_punch(p_tool)
        panel.select_die(d_tool)
        panel.align_tooling()
        tooling = panel.tooling_setup
        panel._sync_tooling_to_active_bend()

        cache = StepEvaluationCache()

        # Physical benchmark: never turn virtual active-bend tool length into
        # claimed source-CAD evidence. Both modes use actual fixed CAD tools.
        if match_active_bend_length:
            raise ValueError("Physical benchmark requires fixed-length real tooling")
        tooling.set_match_active_bend_length(False)
        tooling.set_native_sections(section_offsets_xm, section_offsets_xm)
        use_2d_screening = (mode == "2D_RISK")
        verified = verified_fixed_tool_spans(tooling, ram_z_mm=float(panel.press_cycle.ram_z_mm))
        punch_span = verified.punch_span_xm if use_2d_screening else None
        die_span = verified.die_span_xm if use_2d_screening else None

        t0 = time.perf_counter()
        with count_native_kernel_calls() as kernel_counter, count_station_screenings() as screening_counter:
            if direction.upper() == "FORWARD":
                report = search_bend_sequence_25d(
                    session,
                    panel.press_cycle,
                    tooling,
                    config=sampling_cfg,
                    weights=weights,
                    match_active_bend_length=match_active_bend_length,
                    allow_end_zone_collision=allow_end_zone_collision,
                    end_zone_length_mm=end_zone_length_mm,
                    max_states=max_states,
                    max_checks=max_checks,
                    max_search_time_s=max_search_time_s,
                    optimize_cost=optimize_cost,
                    cache=cache,
                    search_direction="FORWARD",
                    experimental_multi_station_2d=use_2d_screening,
                    experimental_2d_risk_ranking=use_2d_screening,
                    verified_punch_span_xm=punch_span,
                    verified_die_span_xm=die_span,
                )
            elif direction.upper() == "BACKWARD":
                report = search_bend_sequence_backward_25d(
                    session,
                    panel.press_cycle,
                    tooling,
                    config=sampling_cfg,
                    weights=weights,
                    match_active_bend_length=match_active_bend_length,
                    allow_end_zone_collision=allow_end_zone_collision,
                    end_zone_length_mm=end_zone_length_mm,
                    max_states=max_states,
                    max_checks=max_checks,
                    max_search_time_s=max_search_time_s,
                    optimize_cost=optimize_cost,
                    cache=cache,
                    experimental_multi_station_2d=use_2d_screening,
                    experimental_2d_risk_ranking=use_2d_screening,
                    verified_punch_span_xm=punch_span,
                    verified_die_span_xm=die_span,
                )
            else:
                raise ValueError(f"Unknown direction: {direction}")
        wall_s = time.perf_counter() - t0

        def _file_digest(path):
            return sha256(Path(path).read_bytes()).hexdigest()
        model_id = f"{fixture_name}:sha256={_file_digest(fixture_path)}:unfold={unfold.Name}"
        tooling_id = (
            f"punch={punch_name}:{_file_digest(verified.punch_source)}"
            f":die={die_name}:{_file_digest(verified.die_source)}"
            f":p_span={verified.punch_span_xm}:d_span={verified.die_span_xm}"
            f":p_sections={verified.punch_sections_xm}:d_sections={verified.die_sections_xm}"
        )
        placement_id = f"dir={direction}:match_len={match_active_bend_length}:shift=0"
        tol_sig = (
            f"near={sampling_cfg.near_tolerance_mm}"
            f":vol={sampling_cfg.volume_tolerance_mm3}"
            f":contact={sampling_cfg.contact_distance_tolerance_mm}"
            f":arc={sampling_cfg.arc_segments}:phase={sampling_cfg.max_phase_time_s}"
            f":strict_end_zone={allow_end_zone_collision}"
        )

        measurement = from_search_report(
            report,
            mode=mode,
            occt_boolean_calls=kernel_counter.common_calls,
            occt_boolean_elapsed_s=kernel_counter.common_elapsed_s,
            model_id=model_id,
            tooling_setup_id=tooling_id,
            placement_id=placement_id,
            tolerance_signature=tol_sig,
        )
        kernel_snapshot = kernel_counter.to_dict()
        kernel_snapshot["verified_tool_spans"] = verified.to_dict()
        kernel_snapshot["physical_length_mode"] = "LIBRARY_FIXED"
        kernel_snapshot["section_offsets_xm"] = list(section_offsets_xm)
        kernel_snapshot["search_report"] = report.to_dict()
        kernel_snapshot["step_diagnostics"] = [
            rec.to_dict() for rec in cache._step_cache.values()
        ]
        kernel_snapshot["multi_station_execution"] = screening_counter.to_dict()
        return measurement, kernel_snapshot, wall_s
    finally:
        try:
            App.closeDocument(doc.Name)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Unified Benchmark: 2D Screening vs Baseline")
    parser.add_argument("--fixture", type=str, default="Z-(ok).FCStd", help="Part model fixture name (default: Z-(ok).FCStd)")
    parser.add_argument("--direction", type=str, default="FORWARD", choices=["FORWARD", "BACKWARD"], help="Search direction (default: FORWARD)")
    parser.add_argument("--punch", type=str, default="PUNCH_AP_1017_88°_H104,65_R0,8", help="Punch tool name")
    parser.add_argument("--die", type=str, default="T-DIE_AT_3015_88°_V12_H80", help="Die tool name")
    parser.add_argument("--arc-segments", type=int, default=128, help="Adaptive sampling arc segments (default: 128)")
    parser.add_argument("--phase-time", type=float, default=180.0, help="Max time per phase in seconds (default: 180.0)")
    parser.add_argument("--search-time", type=float, default=600.0, help="Max search time in seconds (default: 600.0)")
    parser.add_argument("--max-states", type=int, default=20, help="Max search states (default: 20)")
    parser.add_argument("--max-checks", type=int, default=50, help="Max step evaluations (default: 50)")
    parser.add_argument(
        "--sections-xm", type=str, default="",
        help="Physical fixed-CAD tool section centers in machine X, comma-separated (e.g.=-50,50). Empty=single section.",
    )
    parser.add_argument("--output", type=str, default="reports/benchmark_2d_vs_baseline_report.json", help="Output report JSON path")
    args = parser.parse_args()
    offsets = tuple(float(x.strip()) for x in args.sections_xm.split(",") if x.strip())
    if len(offsets) == 1:
        parser.error("--sections-xm needs at least two centers or none")

    git_commit, git_dirty = get_git_info()
    sampling_cfg = AdaptiveSamplingConfig(
        early_stop_on_blocking_collision=True,
        max_phase_time_s=args.phase_time,
        arc_segments=args.arc_segments,
        near_tolerance_mm=0.5,
        volume_tolerance_mm3=1e-4,
        contact_distance_tolerance_mm=1e-4,
    )
    weights = OperationWeights(flip=20.0, turn_per_90_deg=10.0, shift_fixed=5.0)

    print("=" * 70)
    print(f"BENCHMARK: 2D SCREENING VS BASELINE ({args.direction}) ON {args.fixture}")
    print("=" * 70, flush=True)

    # 1. Run Mode A: BASELINE
    print(f"\n--- [1/2] Running Mode A: BASELINE ({args.direction}) ---", flush=True)
    baseline_meas, baseline_kernel, base_wall_s = run_benchmark_run(
        mode="BASELINE",
        direction=args.direction,
        fixture_name=args.fixture,
        punch_name=args.punch,
        die_name=args.die,
        sampling_cfg=sampling_cfg,
        weights=weights,
        max_search_time_s=args.search_time,
        max_states=args.max_states,
        max_checks=args.max_checks,
        section_offsets_xm=offsets,
    )
    print(f"BASELINE completed in {base_wall_s:.2f} s: status={baseline_meas.status}, seq={baseline_meas.found_sequence}", flush=True)
    print(f"  Stage C attempts: {baseline_meas.stage_c_validation_attempts}, OCCT common: {baseline_meas.occt_boolean_calls}, distance: {baseline_kernel.get('distance_calls')}", flush=True)

    # 2. Run Mode B: 2D_RISK
    print(f"\n--- [2/2] Running Mode B: 2D_RISK ({args.direction}) ---", flush=True)
    screened_meas, screened_kernel, screen_wall_s = run_benchmark_run(
        mode="2D_RISK",
        direction=args.direction,
        fixture_name=args.fixture,
        punch_name=args.punch,
        die_name=args.die,
        sampling_cfg=sampling_cfg,
        weights=weights,
        max_search_time_s=args.search_time,
        max_states=args.max_states,
        max_checks=args.max_checks,
        section_offsets_xm=offsets,
    )
    print(f"2D_RISK completed in {screen_wall_s:.2f} s: status={screened_meas.status}, seq={screened_meas.found_sequence}", flush=True)
    print(f"  Stage C attempts: {screened_meas.stage_c_validation_attempts}, OCCT common: {screened_meas.occt_boolean_calls}, distance: {screened_kernel.get('distance_calls')}", flush=True)

    # 3. Compare measurements
    comp = compare_measurements(baseline_meas, screened_meas)
    observed = screened_kernel["multi_station_execution"]
    comp["screening_evidence"] = {
        "status": "ACTUAL_2D_STATIONS_EVALUATED" if observed["evaluated_sections"] > 0 else "NO_VERIFIED_2D_STATION_EVALUATION",
        "sampled_sections": observed["evaluated_sections"],
        "unverified_stations": observed["unverified_stations"],
        "note": "This shows execution only, not safety or causal speedup.",
    }
    comp["kernel_snapshots"] = {
        "baseline": baseline_kernel,
        "screened": screened_kernel,
    }
    comp["metadata"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "fixture": args.fixture,
        "direction": args.direction,
        "punch": args.punch,
        "die": args.die,
        "section_offsets_xm": list(offsets),
        "tooling_mode": "LIBRARY_FIXED_NATIVE_SECTIONS" if offsets else "LIBRARY_FIXED",
        "budgets": {
            "max_search_time_s": args.search_time,
            "max_phase_time_s": args.phase_time,
            "max_states": args.max_states,
            "max_checks": args.max_checks,
            "arc_segments": args.arc_segments,
        },
    }

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(comp, f, indent=2, ensure_ascii=False)

    print(f"\nAuthoritative comparison report written to: {out_path}", flush=True)
    print("2D execution evidence:", comp["screening_evidence"], flush=True)
    print("Savings summary:")
    for k, v in comp["savings"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

