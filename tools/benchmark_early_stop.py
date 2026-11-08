"""Targeted benchmark for inside-phase adaptive early stopping.

Compares early_stop_on_blocking_collision=False vs True on:
- Case A: B02 at s=0 (flange collision during BENDING)
- Case B: B01 at s=0 (control case without collision)
"""
from __future__ import annotations

import os
import sys
import time
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
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig

def run_benchmark():
    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    doc = App.openDocument(str(fixture), True)
    unfold = next(o for o in doc.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc, [unfold])
    panel = _PreviewPanel()
    panel.session = session
    
    p_tool = panel.tool_library.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH)
    d_tool = panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE)
    panel.select_punch(p_tool)
    panel.select_die(d_tool)
    panel.align_tooling()
    
    # Tool length matching bend length
    panel.tooling_setup.set_match_active_bend_length(True, 45.0)
    
    results = {}
    
    # ----------------------------------------------------
    # Case B: Control (B01 at s=0, no collision)
    # ----------------------------------------------------
    print("=== Running Case B: B01 Control (no collision) ===")
    session.set_bend_order(["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"])
    session.select_bend_index(0)
    panel._sync_tooling_to_active_bend()
    session.fold_state.reset_all()
    
    # Control without early stop
    cfg_b_no = AdaptiveSamplingConfig(early_stop_on_blocking_collision=False, max_phase_time_s=30.0)
    t0 = time.perf_counter()
    rep_b_no = check_active_25d_bend_trajectory(session, panel.tooling_setup, config=cfg_b_no)
    t_b_no = time.perf_counter() - t0
    raw_b_no = rep_b_no.raw_report
    
    # Control with early stop
    cfg_b_yes = AdaptiveSamplingConfig(early_stop_on_blocking_collision=True, max_phase_time_s=30.0)
    t0 = time.perf_counter()
    rep_b_yes = check_active_25d_bend_trajectory(session, panel.tooling_setup, config=cfg_b_yes)
    t_b_yes = time.perf_counter() - t0
    raw_b_yes = rep_b_yes.raw_report
    
    results["Case_B_control"] = {
        "status_without": rep_b_no.status.value,
        "status_with": rep_b_yes.status.value,
        "time_without_s": round(t_b_no, 3),
        "time_with_s": round(t_b_yes, 3),
        "states_without": raw_b_no.total_states_evaluated if raw_b_no else 0,
        "states_with": raw_b_yes.total_states_evaluated if raw_b_yes else 0,
        "solid_rebuilds_without": sum(p.solid_reconstructions_count for p in raw_b_no.phase_results) if raw_b_no else 0,
        "solid_rebuilds_with": sum(p.solid_reconstructions_count for p in raw_b_yes.phase_results) if raw_b_yes else 0,
        "queries_without": sum(p.collision_queries_count for p in raw_b_no.phase_results) if raw_b_no else 0,
        "queries_with": sum(p.collision_queries_count for p in raw_b_yes.phase_results) if raw_b_yes else 0,
    }
    print("Case B done:", results["Case_B_control"])
    
    # ----------------------------------------------------
    # Case A: Collision inside trajectory (B02 at s=0, with B01 folded at 90 deg)
    # ----------------------------------------------------
    print("=== Running Case A: B02 at s=0 (Collision inside trajectory) ===")
    session.select_bend_index(1)
    panel._sync_tooling_to_active_bend()
    session.fold_state.reset_all()
    session.fold_state.set_angle("B01", 90.0)
    
    # With early stop
    cfg_a_yes = AdaptiveSamplingConfig(early_stop_on_blocking_collision=True, max_phase_time_s=30.0)
    t0 = time.perf_counter()
    rep_a_yes = check_active_25d_bend_trajectory(session, panel.tooling_setup, config=cfg_a_yes)
    t_a_yes = time.perf_counter() - t0
    raw_a_yes = rep_a_yes.raw_report
    
    # Without early stop (limit phase time to 15s to keep benchmark bounded)
    cfg_a_no = AdaptiveSamplingConfig(early_stop_on_blocking_collision=False, max_phase_time_s=15.0)
    t0 = time.perf_counter()
    rep_a_no = check_active_25d_bend_trajectory(session, panel.tooling_setup, config=cfg_a_no)
    t_a_no = time.perf_counter() - t0
    raw_a_no = rep_a_no.raw_report
    
    results["Case_A_collision"] = {
        "status_without": rep_a_no.status.value,
        "status_with": rep_a_yes.status.value,
        "time_without_s": round(t_a_no, 3),
        "time_with_s": round(t_a_yes, 3),
        "states_without": raw_a_no.total_states_evaluated if raw_a_no else 0,
        "states_with": raw_a_yes.total_states_evaluated if raw_a_yes else 0,
        "solid_rebuilds_without": sum(p.solid_reconstructions_count for p in raw_a_no.phase_results) if raw_a_no else 0,
        "solid_rebuilds_with": sum(p.solid_reconstructions_count for p in raw_a_yes.phase_results) if raw_a_yes else 0,
        "queries_without": sum(p.collision_queries_count for p in raw_a_no.phase_results) if raw_a_no else 0,
        "queries_with": sum(p.collision_queries_count for p in raw_a_yes.phase_results) if raw_a_yes else 0,
        "uninspected_intervals_with": [
            (p.phase_name, getattr(p, "uninspected_intervals", []))
            for p in raw_a_yes.phase_results if getattr(p, "uninspected_intervals", [])
        ] if raw_a_yes else [],
    }
    print("Case A done:", results["Case_A_collision"])
    
    panel.close()
    panel.deleteLater()
    App.closeDocument(doc.Name)
    
    import json
    print("\nFINAL_BENCHMARK_JSON:")
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_benchmark()

