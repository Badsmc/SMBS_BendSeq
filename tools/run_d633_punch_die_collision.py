"""run_d633_punch_die_collision.py - Deterministic Runner for D6.3.3 Punch/Die Collision & Machine Qualification.

Executes physical collision analysis and machine setup qualification on real SheetMetal fixtures:
- `L-(ok).FCStd` (Single bend 90°)
- `Z-(ok).FCStd` (Multi bend +90°, -90°)
- `135-(ok).FCStd` (Multi bend +45°, -45°)
against verified tooling `puancon.FCStd` (Punch) and `matrica.FCStd` (Die).

Tests key phases of the press-brake working cycle:
1. Single 100mm section baseline (verifies INSUFFICIENT_TOOL_LENGTH qualification rejection).
2. Explicitly configured 2x100mm = 200mm tool assembly (FULL_TOOL_COVERAGE qualification).
3. Evaluates geometric collision: CLEAR, NEAR_CONTACT, TOUCHING, PENETRATION.
4. Distinguishes discrete sampled poses from continuous trajectory certification.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_die,
    load_native_punch,
    set_punch_ram_z,
    check_tool_coverage,
)
from smbs_core.kinematics2d.native_collision_3d import (
    validate_workpiece_tooling_pose,
    qualify_machine_setup,
    CollisionClass,
)


def find_fixture_path(filename: str) -> Path:
    candidates = [
        ROOT / "BendSeq_Fixtures_Source" / filename,
        Path("/Users/badmc/Desktop/bend_Seq/BendSeq_Fixtures_Source") / filename,
        Path("/Users/badmc/Desktop/bend_Seq/SMBS/SMBendTests") / filename,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"Could not find fixture {filename}")


def test_fixture_cycle(
    fixture_filename: str,
    punch_file: Path,
    die_file: Path,
    tool_offsets_xm: Optional[List[float]] = None,
) -> dict:
    import FreeCAD as App
    from freecad_smbs.commands.pressbrake_2d import build_part_session

    fixture_path = find_fixture_path(fixture_filename)
    die = load_native_die(die_file, section_offsets_xm=tool_offsets_xm)
    punch_base = load_native_punch(punch_file, ram_z_mm=50.0, section_offsets_xm=tool_offsets_xm)

    doc = App.openDocument(str(fixture_path), True)
    try:
        unfold = [o for o in doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
        session = build_part_session(doc, [unfold])
        state = session.fold_state

        fixture_report = {
            "fixture": fixture_filename,
            "tool_setup": "2x100mm_ASSEMBLY" if tool_offsets_xm else "1x100mm_SINGLE",
            "tool_length_mm": punch_base.length_mm,
            "bends_evaluated": [],
        }

        for bend in state.ordered_bends:
            bend_id = bend.bend_id
            bz = bend.bend_zone
            p0_cad, p1_cad = bend.axis_xyz
            bend_len = math.dist(p0_cad, p1_cad)
            bend_span_xm = (-0.5 * bend_len, 0.5 * bend_len)

            punch_cov = check_tool_coverage(bend_span_xm, punch_base.span_xm, punch_base.sections)
            die_cov = check_tool_coverage(bend_span_xm, die.span_xm, die.sections)

            coupled = CoupledAirBendingKinematics(
                die_opening_v_mm=die.v_opening_mm,
                die_shoulder_radius_mm=die.shoulder_radius_mm,
                die_angle_deg=die.included_angle_deg,
                die_v_depth_mm=die.v_depth_mm,
                punch_tip_radius_mm=punch_base.tip_radius_mm,
                punch_included_angle_deg=punch_base.included_angle_deg,
                sheet_thickness_mm=bz.thickness_mm,
                sheet_inner_radius_mm=bz.inner_radius_mm,
                die_top_z_mm=0.0,
            )

            target_angle = abs(float(bend.angle_deg))
            test_phases = [
                ("APPROACH", 0.0, 50.0, bz.thickness_mm),
                ("CONTACT", 0.0, 1.5, bz.thickness_mm),
                ("BENDING_HALF", 0.5 * target_angle, coupled.ram_z_for_angle(0.5 * target_angle) + (bz.inner_radius_mm - punch_base.tip_radius_mm) + 0.1, coupled.support_plane_zm(0.5 * target_angle) + bz.thickness_mm),
                ("BOTTOM_TARGET", target_angle, coupled.ram_z_for_angle(target_angle) + (bz.inner_radius_mm - punch_base.tip_radius_mm) + 0.1, coupled.support_plane_zm(target_angle) + bz.thickness_mm),
                ("RETRACT", target_angle, 40.0, coupled.support_plane_zm(target_angle) + bz.thickness_mm),
            ]

            phase_records = []
            all_safe = True

            for p_name, deg, ram_z, supp_z in test_phases:
                state.set_angle(bend_id, deg if bend.angle_deg >= 0 else -deg)
                solid = build_multi_bend_sheet_solid(state, session.panel_build, segments=32)

                tf = WorkpiecePlacement3D.compute_placement(state, bend_id, support_plane_zm=supp_z, align_bisector=True)
                mat_wp = App.Matrix(
                    tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
                    tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
                    tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
                    0.0, 0.0, 0.0, 1.0,
                )
                wp_machine = solid.shape.copy()
                wp_machine.transformShape(mat_wp)

                punch_step = set_punch_ram_z(punch_base, ram_z)
                col_eval = validate_workpiece_tooling_pose(
                    wp_machine,
                    punch_step.machine_shape,
                    die.machine_shape,
                )

                if not col_eval.is_safe:
                    all_safe = False

                phase_records.append({
                    "phase": p_name,
                    "angle_deg": deg,
                    "ram_z_mm": ram_z,
                    "support_plane_zm": supp_z,
                    "status": col_eval.status.value,
                    "is_safe": col_eval.is_safe,
                    "punch_clearance_mm": col_eval.punch_result.distance_mm,
                    "punch_overlap_volume_mm3": col_eval.punch_result.overlap_volume_mm3,
                    "die_clearance_mm": col_eval.die_result.distance_mm,
                    "die_overlap_volume_mm3": col_eval.die_result.overlap_volume_mm3,
                })

            qual = qualify_machine_setup(col_eval, punch_cov, die_cov)
            bend_record = {
                "bend_id": bend_id,
                "bend_length_mm": bend_len,
                "punch_coverage": punch_cov.status,
                "die_coverage": die_cov.status,
                "is_qualified": qual.is_qualified,
                "qualification_status": qual.qualification_status,
                "phases": phase_records,
            }
            fixture_report["bends_evaluated"].append(bend_record)

            print(
                f"SMBS D6.3.3: Fixture={fixture_filename:14s} Bend={bend_id:4s} "
                f"Setup={fixture_report['tool_setup']:18s} Qual={qual.qualification_status:24s} "
                f"AllPosesSafe={all_safe}"
            )

        return fixture_report

    finally:
        App.closeDocument(doc.Name)


def run_full_validation() -> dict:
    t_start = time.perf_counter()
    import FreeCAD as App

    punch_path = ROOT / "toollibrary" / "punch" / "puancon.FCStd"
    die_path = ROOT / "toollibrary" / "die" / "matrica.FCStd"

    report = {
        "schema": "smbs-d633-punch-die-collision-v3",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freecad_version": ".".join(map(str, App.Version()[:3])),
        "punch_file": str(punch_path),
        "die_file": str(die_path),
        "tests": [],
        "trajectory_status": "UNVERIFIED_DISCRETE_SAMPLES_ONLY",
        "overall_status": "NOT_RUN",
    }

    # Test Matrix:
    # 1. Baseline: L-(ok).FCStd with single 100mm section -> Must fail with INSUFFICIENT_PUNCH_COVERAGE
    rep_l_single = test_fixture_cycle("L-(ok).FCStd", punch_path, die_path, tool_offsets_xm=None)
    report["tests"].append(rep_l_single)

    # 2. Configured Assembly: L-(ok).FCStd with 2x100mm = 200mm sections -> Must QUALIFY
    rep_l_assembly = test_fixture_cycle("L-(ok).FCStd", punch_path, die_path, tool_offsets_xm=[-50.0, 50.0])
    report["tests"].append(rep_l_assembly)

    # 3. Z-(ok).FCStd (+90, -90) with 2x100mm assembly -> Must QUALIFY
    rep_z_assembly = test_fixture_cycle("Z-(ok).FCStd", punch_path, die_path, tool_offsets_xm=[-50.0, 50.0])
    report["tests"].append(rep_z_assembly)

    # 4. 135-(ok).FCStd (+45, -45) with 2x100mm assembly -> Must QUALIFY
    rep_135_assembly = test_fixture_cycle("135-(ok).FCStd", punch_path, die_path, tool_offsets_xm=[-50.0, 50.0])
    report["tests"].append(rep_135_assembly)

    # Determine overall status
    single_rejected = rep_l_single["bends_evaluated"][0]["qualification_status"] == "INSUFFICIENT_PUNCH_COVERAGE"
    l_ok = rep_l_assembly["bends_evaluated"][0]["qualification_status"] == "QUALIFIED"
    z_ok = all(b["qualification_status"] == "QUALIFIED" for b in rep_z_assembly["bends_evaluated"])
    a135_ok = all(b["qualification_status"] == "QUALIFIED" for b in rep_135_assembly["bends_evaluated"])

    if single_rejected and l_ok and z_ok and a135_ok:
        report["overall_status"] = "PASS"
    else:
        report["overall_status"] = "FAIL"

    report["total_elapsed_ms"] = (time.perf_counter() - t_start) * 1000.0
    return report


def main():
    report = run_full_validation()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = report_file(f"d633_punch_die_collision_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nD6.3.3 Report written to: {out_path}")
    print(f"Overall status: {report['overall_status']}")


if __name__ == "__main__":
    main()
