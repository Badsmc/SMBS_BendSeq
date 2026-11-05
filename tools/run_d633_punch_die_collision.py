"""run_d633_punch_die_collision.py - Deterministic Runner for D6.3.3 Punch/Die Collision.

Executes physical collision analysis on real SheetMetal fixture `L-(ok).FCStd`
against verified tooling `puancon.FCStd` (Punch) and `matrica.FCStd` (Die).

Tests 5 key phases of the press-brake working cycle:
1. APPROACH: Workpiece flat on die table; punch elevated at Z_ram = 50.0 mm.
2. CONTACT: Workpiece flat on die table; punch touching sheet at Z_ram = 1.5 mm.
3. BENDING_45: Workpiece partially bent to 45.0°; punch penetrating into die cavity.
4. BOTTOM_90: Workpiece fully bent to 90.0°; bottom dead center forming engagement.
5. RETRACT: Workpiece at 90.0°; punch retracted upward to Z_ram = 40.0 mm.

Outputs verified diagnostic JSON evidence to `reports/d633_punch_die_collision_<timestamp>.json`
via `smbs_core.report_paths.report_file()`.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch, set_punch_ram_z
from smbs_core.kinematics2d.native_collision_3d import (
    validate_workpiece_tooling_pose,
    CollisionClass,
)


def find_fixture_path() -> Path:
    candidates = [
        ROOT / "BendSeq_Fixtures_Source" / "L-(ok).FCStd",
        Path("/Users/badmc/Desktop/bend_Seq/BendSeq_Fixtures_Source/L-(ok).FCStd"),
        Path("/Users/badmc/Desktop/bend_Seq/SMBS/SMBendTests/L-(ok).FCStd"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("Could not find fixture L-(ok).FCStd")


def run_collision_validation() -> dict:
    t_start = time.perf_counter()
    import FreeCAD as App
    from freecad_smbs.commands.pressbrake_2d import build_part_session

    fixture_path = find_fixture_path()
    punch_path = ROOT / "toollibrary" / "punch" / "puancon.FCStd"
    die_path = ROOT / "toollibrary" / "die" / "matrica.FCStd"

    report = {
        "schema": "smbs-d633-punch-die-collision-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "fixture": str(fixture_path),
        "punch_file": str(punch_path),
        "die_file": str(die_path),
        "freecad_version": ".".join(map(str, App.Version()[:3])),
        "cycle_phases": [],
        "overall_status": "NOT_RUN",
    }

    # Load CAD tools
    die = load_native_die(die_path)
    punch_base = load_native_punch(punch_path, ram_z_mm=50.0)

    # Load workpiece session
    doc = App.openDocument(str(fixture_path), True)
    try:
        unfold = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
        session = build_part_session(doc, [unfold])
        state = session.fold_state
        bend = state.bends["B01"]
        bz = bend.bend_zone

        coupled = CoupledAirBendingKinematics(
            die_opening_v_mm=die.v_opening_mm,
            die_shoulder_radius_mm=die.shoulder_radius_mm,
            die_angle_deg=die.included_angle_deg,
            die_v_depth_mm=16.500562,
            punch_tip_radius_mm=punch_base.tip_radius_mm,
            punch_included_angle_deg=punch_base.included_angle_deg,
            sheet_thickness_mm=bz.thickness_mm,
            sheet_inner_radius_mm=bz.inner_radius_mm,
            die_top_z_mm=0.0,
        )

        test_phases = [
            {
                "phase": "APPROACH",
                "bend_angle_deg": 0.0,
                "ram_z_mm": 50.0,
                "support_plane_zm": bz.thickness_mm,
                "is_forming": False,
                "expected_status": CollisionClass.CLEAR,
            },
            {
                "phase": "CONTACT",
                "bend_angle_deg": 0.0,
                "ram_z_mm": 1.5,
                "support_plane_zm": bz.thickness_mm,
                "is_forming": True,
                "expected_status": CollisionClass.TOUCHING,
            },
            {
                "phase": "BENDING_45",
                "bend_angle_deg": 45.0,
                "ram_z_mm": coupled.ram_z_for_angle(45.0) + (bz.inner_radius_mm - punch_base.tip_radius_mm),
                "support_plane_zm": coupled.support_plane_zm(45.0) + bz.thickness_mm,
                "is_forming": True,
                "expected_status": CollisionClass.TOUCHING,
            },
            {
                "phase": "BOTTOM_90",
                "bend_angle_deg": 90.0,
                "ram_z_mm": coupled.ram_z_for_angle(90.0) + (bz.inner_radius_mm - punch_base.tip_radius_mm),
                "support_plane_zm": coupled.support_plane_zm(90.0) + bz.thickness_mm,
                "is_forming": True,
                "expected_status": CollisionClass.TOUCHING,
            },
            {
                "phase": "RETRACT",
                "bend_angle_deg": 90.0,
                "ram_z_mm": 40.0,
                "support_plane_zm": coupled.support_plane_zm(90.0) + bz.thickness_mm,
                "is_forming": False,
                "expected_status": CollisionClass.CLEAR,
            },
        ]

        all_phases_ok = True

        for p_info in test_phases:
            p_name = p_info["phase"]
            deg = p_info["bend_angle_deg"]
            ram_z = p_info["ram_z_mm"]
            supp_z = p_info["support_plane_zm"]
            is_forming = p_info["is_forming"]

            # Construct solid at target angle
            state.set_angle("B01", deg)
            solid = build_multi_bend_sheet_solid(state, session.panel_build, segments=32)

            # Compute rigid 3D placement into Machine Space
            tf = WorkpiecePlacement3D.compute_placement(state, "B01", support_plane_zm=supp_z, align_bisector=True)
            mat_wp = App.Matrix(
                tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                0.0, 0.0, 0.0, 1.0,
            )
            wp_machine = solid.shape.copy()
            wp_machine.transformShape(mat_wp)

            # Punch at current ram_z
            punch_step = set_punch_ram_z(punch_base, ram_z)

            # Evaluate Collision
            step_eval = validate_workpiece_tooling_pose(
                wp_machine,
                punch_step.machine_shape,
                die.machine_shape,
                is_forming_state=is_forming,
            )

            phase_record = {
                "phase": p_name,
                "bend_angle_deg": deg,
                "ram_z_mm": ram_z,
                "support_plane_zm": supp_z,
                "is_forming": is_forming,
                "status": step_eval.status.value,
                "is_safe": step_eval.is_safe,
                "punch_clearance_mm": step_eval.punch_result.distance_mm,
                "punch_overlap_volume_mm3": step_eval.punch_result.overlap_volume_mm3,
                "die_clearance_mm": step_eval.die_result.distance_mm,
                "die_overlap_volume_mm3": step_eval.die_result.overlap_volume_mm3,
                "diagnostics": step_eval.diagnostics,
                "elapsed_ms": step_eval.elapsed_ms,
            }
            report["cycle_phases"].append(phase_record)

            if not step_eval.is_safe:
                all_phases_ok = False

            print(
                f"SMBS D6.3.3: {p_name:12s} | Status={step_eval.status.value:10s} "
                f"Safe={step_eval.is_safe} PunchDist={step_eval.punch_result.distance_mm:.2f}mm "
                f"DieDist={step_eval.die_result.distance_mm:.2f}mm ({step_eval.elapsed_ms:.1f}ms)"
            )

        report["overall_status"] = "PASS" if all_phases_ok else "FAIL"
        report["total_elapsed_ms"] = (time.perf_counter() - t_start) * 1000.0

    finally:
        App.closeDocument(doc.Name)

    return report


def main():
    report = run_collision_validation()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = report_file(f"d633_punch_die_collision_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nD6.3.3 Report written to: {out_path}")
    print(f"Overall status: {report['overall_status']}")
    if report["overall_status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
