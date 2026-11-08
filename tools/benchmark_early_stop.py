"""Authoritative Comparative Benchmark for Inside-Phase Adaptive Early Stopping.

Compares early_stop_on_blocking_collision=False vs True under strictly identical settings:
- Case A: Confirmed forbidden collision inside BENDING (B05 with prior B01-B04 folded).
- Case B: Inspection without collisions (B01 flat starting sheet).
  NOTE: As per project rules, B01 produces UNVERIFIED (INTERVAL_UNRESOLVED)
  due to the phase time budget being reached before continuous swept proof.
  This is explicitly marked as INCOMPLETE/UNVERIFIED, not a completed safe control.

Generates a machine-readable JSON report at reports/benchmark_early_stop_report.json.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide import QtWidgets

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.physical_validation_25d import check_active_25d_bend_trajectory
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
    CoupledAirBendingKinematics,
    is_blocking_collision,
)
from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT))
        return out.decode("ascii").strip()
    except Exception:
        return "UNKNOWN"


def run_benchmark():
    git_commit = get_git_commit()
    fixture_name = "box.100out-50in-(ok).FCStd"
    fixture_path = demo_part_path(fixture_name)
    doc = App.openDocument(str(fixture_path), True)
    unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc, [unfold])
    panel = _PreviewPanel()
    panel.session = session

    punch_name = "PUNCH_AP_1017_88°_H104,65_R0,8"
    die_name = "T-DIE_AT_3015_88°_V12_H80"
    p_tool = panel.tool_library.load_tool(punch_name, ToolKind.PUNCH)
    d_tool = panel.tool_library.load_tool(die_name, ToolKind.DIE)
    panel.select_punch(p_tool)
    panel.select_die(d_tool)
    panel.align_tooling()
    tooling = panel.tooling_setup

    max_phase_time_s = 15.0

    report_data = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
            "fixture": fixture_name,
            "pressbrake_punch": punch_name,
            "pressbrake_die": die_name,
            "max_phase_time_s": max_phase_time_s,
            "note": "Settings, tooling, and tolerances are strictly identical between with/without early stop runs.",
        },
        "cases": {},
    }

    # =========================================================================
    # Case A: Confirmed Forbidden Collision inside BENDING
    # Workpiece B05 with prior completed bends B01, B02, B03, B04 folded at 90°
    # =========================================================================
    print("=== Running Case A: Confirmed Collision inside BENDING (B05) ===", flush=True)
    active_b5 = session.fold_state.bends["B05"]
    ax5 = getattr(active_b5, "axis_xyz", None)
    blen5 = math.dist(ax5[0], ax5[1]) if ax5 else 46.6

    punch5 = load_native_punch(tooling.punch.source_file, ram_z_mm=50.0, virtual_length_mm=blen5)
    die5 = load_native_die(tooling.die.source_file, virtual_length_mm=blen5)

    prior_b5 = {
        "B01": float(session.fold_state.bends["B01"].angle_deg),
        "B02": float(session.fold_state.bends["B02"].angle_deg),
        "B03": float(session.fold_state.bends["B03"].angle_deg),
        "B04": float(session.fold_state.bends["B04"].angle_deg),
    }

    val5 = AdaptiveTrajectoryValidator3D(session.fold_state, session.panel_build, punch5, die5)
    bz5 = active_b5.bend_zone
    coupled5 = CoupledAirBendingKinematics(
        die_opening_v_mm=val5.effective_v,
        die_shoulder_radius_mm=die5.shoulder_radius_mm,
        die_angle_deg=die5.included_angle_deg,
        die_v_depth_mm=die5.v_depth_mm,
        punch_tip_radius_mm=punch5.tip_radius_mm,
        punch_included_angle_deg=punch5.included_angle_deg,
        sheet_thickness_mm=bz5.thickness_mm,
        sheet_inner_radius_mm=bz5.inner_radius_mm,
        die_top_z_mm=0.0,
    )
    abs_target_deg5 = abs(active_b5.angle_deg)
    sign5 = 1.0 if active_b5.angle_deg >= 0 else -1.0

    def gen_bending_b5(u: float):
        theta = u * abs_target_deg5
        ram_z = coupled5.ram_z_for_angle(theta)
        supp_z = -coupled5.stroke_for_angle(theta) + bz5.outer_radius_mm
        return (sign5 * theta, ram_z, supp_z)

    # 1. Without early stop
    cfg_a_no = AdaptiveSamplingConfig(early_stop_on_blocking_collision=False, max_phase_time_s=max_phase_time_s)
    val5.set_config(cfg_a_no)
    val5.clear_caches()
    session.fold_state.reset_all()
    t0 = time.perf_counter()
    pr_a_no = val5.validate_phase("BENDING", "B05", prior_b5, gen_bending_b5)
    t_a_no = time.perf_counter() - t0

    # 2. With early stop
    cfg_a_yes = AdaptiveSamplingConfig(early_stop_on_blocking_collision=True, max_phase_time_s=max_phase_time_s)
    val5.set_config(cfg_a_yes)
    val5.clear_caches()
    session.fold_state.reset_all()
    t0 = time.perf_counter()
    pr_a_yes = val5.validate_phase("BENDING", "B05", prior_b5, gen_bending_b5)
    t_a_yes = time.perf_counter() - t0

    # Extract first collision evidence
    first_col_state = next((s for s in pr_a_yes.states if is_blocking_collision(s)), None)
    col_info = None
    if first_col_state:
        p_res = first_col_state.collision_report.punch_result
        d_res = first_col_state.collision_report.die_result
        col_info = {
            "phase": "BENDING",
            "path_u": float(first_col_state.path_u),
            "angle_deg": float(first_col_state.signed_angle_deg),
            "punch_overlap_volume_mm3": float(getattr(p_res, "overlap_volume_mm3", 0.0)),
            "die_overlap_volume_mm3": float(getattr(d_res, "overlap_volume_mm3", 0.0)),
            "reason": first_col_state.collision_report.diagnostics[0] if first_col_state.collision_report.diagnostics else "",
        }

    report_data["cases"]["Case_A_collision_inside_bending"] = {
        "description": "B05 BENDING phase with B01..B04 at 90°. Severe central punch penetration occurs.",
        "active_bend": "B05",
        "prior_completed_angles": prior_b5,
        "tool_length_mm": blen5,
        "first_blocking_collision": col_info,
        "without_early_stop": {
            "status": pr_a_no.evidence_status.value,
            "elapsed_s": round(t_a_no, 3),
            "states_evaluated": len(pr_a_no.states),
            "solid_rebuilds": pr_a_no.solid_reconstructions_count,
            "collision_queries": pr_a_no.collision_queries_count,
            "max_punch_overlap_volume_mm3": round(float(pr_a_no.max_punch_overlap_volume_mm3), 4),
            "max_die_overlap_volume_mm3": round(float(pr_a_no.max_die_overlap_volume_mm3), 4),
            "unresolved_intervals": pr_a_no.unresolved_intervals,
            "uninspected_intervals": getattr(pr_a_no, "uninspected_intervals", []),
        },
        "with_early_stop": {
            "status": pr_a_yes.evidence_status.value,
            "elapsed_s": round(t_a_yes, 3),
            "states_evaluated": len(pr_a_yes.states),
            "solid_rebuilds": pr_a_yes.solid_reconstructions_count,
            "collision_queries": pr_a_yes.collision_queries_count,
            "max_punch_overlap_volume_mm3": round(float(pr_a_yes.max_punch_overlap_volume_mm3), 4),
            "max_die_overlap_volume_mm3": round(float(pr_a_yes.max_die_overlap_volume_mm3), 4),
            "unresolved_intervals": pr_a_yes.unresolved_intervals,
            "uninspected_intervals": getattr(pr_a_yes, "uninspected_intervals", []),
        },
        "speedup_factor": round(t_a_no / max(t_a_yes, 1e-6), 2),
    }
    print("Case A done:", report_data["cases"]["Case_A_collision_inside_bending"], flush=True)

    # =========================================================================
    # Case B: Inspection without collisions (B01 flat sheet)
    # Full 5-phase trajectory check under identical budget (15.0 s per phase)
    # =========================================================================
    print("=== Running Case B: Control without collisions (B01) ===", flush=True)
    session.fold_state.reset_all()
    session.set_bend_order(["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"])
    session.select_bend_index(0)
    panel._sync_tooling_to_active_bend()
    tooling.set_match_active_bend_length(True, 45.0)

    # 1. Without early stop
    cfg_b_no = AdaptiveSamplingConfig(early_stop_on_blocking_collision=False, max_phase_time_s=max_phase_time_s)
    session.fold_state.reset_all()
    t0 = time.perf_counter()
    rep_b_no = check_active_25d_bend_trajectory(session, tooling, config=cfg_b_no)
    t_b_no = time.perf_counter() - t0
    raw_b_no = rep_b_no.raw_report

    # 2. With early stop
    cfg_b_yes = AdaptiveSamplingConfig(early_stop_on_blocking_collision=True, max_phase_time_s=max_phase_time_s)
    session.fold_state.reset_all()
    t0 = time.perf_counter()
    rep_b_yes = check_active_25d_bend_trajectory(session, tooling, config=cfg_b_yes)
    t_b_yes = time.perf_counter() - t0
    raw_b_yes = rep_b_yes.raw_report

    report_data["cases"]["Case_B_control_without_collision"] = {
        "description": (
            "B01 full 5-phase cycle. NOTE: Because the BENDING phase reaches max_phase_time_s before "
            "exhaustive continuous proof, the overall evidence status is UNVERIFIED (INTERVAL_UNRESOLVED). "
            "In strict accordance with project rules, UNVERIFIED is NOT treated as a safe PASS, and "
            "no speedup is claimed for this incomplete control case."
        ),
        "active_bend": "B01",
        "prior_completed_angles": {},
        "tool_length_mm": 45.0,
        "is_completed_safe_control": False,
        "status_explanation": "BENDING phase reaches max_phase_time_s budget, leaving interval unresolved.",
        "without_early_stop": {
            "status": rep_b_no.status.value,
            "native_status": rep_b_no.native_status,
            "elapsed_s": round(t_b_no, 3),
            "states_evaluated": raw_b_no.total_states_evaluated if raw_b_no else 0,
            "solid_rebuilds": sum(p.solid_reconstructions_count for p in raw_b_no.phase_results) if raw_b_no else 0,
            "collision_queries": sum(p.collision_queries_count for p in raw_b_no.phase_results) if raw_b_no else 0,
            "phases": [
                {
                    "phase": p.phase_name,
                    "status": p.evidence_status.value,
                    "unresolved_intervals": p.unresolved_intervals,
                    "uninspected_intervals": getattr(p, "uninspected_intervals", []),
                }
                for p in (raw_b_no.phase_results if raw_b_no else [])
            ],
        },
        "with_early_stop": {
            "status": rep_b_yes.status.value,
            "native_status": rep_b_yes.native_status,
            "elapsed_s": round(t_b_yes, 3),
            "states_evaluated": raw_b_yes.total_states_evaluated if raw_b_yes else 0,
            "solid_rebuilds": sum(p.solid_reconstructions_count for p in raw_b_yes.phase_results) if raw_b_yes else 0,
            "collision_queries": sum(p.collision_queries_count for p in raw_b_yes.phase_results) if raw_b_yes else 0,
            "phases": [
                {
                    "phase": p.phase_name,
                    "status": p.evidence_status.value,
                    "unresolved_intervals": p.unresolved_intervals,
                    "uninspected_intervals": getattr(p, "uninspected_intervals", []),
                }
                for p in (raw_b_yes.phase_results if raw_b_yes else [])
            ],
        },
    }
    print("Case B done:", report_data["cases"]["Case_B_control_without_collision"], flush=True)

    panel.close()
    panel.deleteLater()
    App.closeDocument(doc.Name)

    out_json = ROOT / "reports" / "benchmark_early_stop_report.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"\nSaved report to: {out_json}", flush=True)


if __name__ == "__main__":
    run_benchmark()
