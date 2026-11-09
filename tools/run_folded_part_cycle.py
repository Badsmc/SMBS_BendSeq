"""Read-only 2D post-bend press-cycle probe on real FreeCAD FoldState sections.

Evaluates BOTTOM and rising RETRACT with the formed part held stationary,
then +/- machine-s withdrawal translations with punch parked. Does not certify
3D safety, continuous collision freedom, or reorientation/TRANSFER.
"""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from time import perf_counter

from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.kinematics2d.contact_oracle_2d import (
    check_machine_section, ContactStatus,
)
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.machine_section_3d import compute_machine_cutting_plane_section
from smbs_core.kinematics2d.tooling_25d import ToolKind, ToolLibrary25D, ToolingSetup2D
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from tools.run_folded_part_withdrawal import (
    _parts, probe_lateral_withdrawal, DEFAULT_PUNCH, DEFAULT_DIE,
)


def probe_punch_retract(polygons, tooling, *,
                        bottom_ram_z_mm: float, parked_ram_z_mm: float,
                        sample_count: int = 21, tolerance_mm: float = 0.02):
    """Raise a real catalog punch while the formed section remains fixed.

    Die is stationary. Each pose checks all section components against both
    real tool contours. A sampled interference is recorded, not suppressed.
    """
    if sample_count < 2 or sample_count > 1001:
        raise ValueError("sample_count must be in [2,1001]")
    if (not isfinite(bottom_ram_z_mm) or not isfinite(parked_ram_z_mm)
            or parked_ram_z_mm < bottom_ram_z_mm):
        raise ValueError("parked ram height must be finite and >= bottom height")
    if not polygons:
        return {"status": "UNVERIFIED_2D", "reason": "empty_part_section", "samples": []}
    samples = []
    try:
        original_ram_z = float(tooling.ram_z_mm)
        original_ram_stroke = float(tooling.ram_stroke_mm)
        original_punch_tf = tooling.punch_machine_transform
        die = tooling.die_polygons_machine(arc_segments=24)
        if die is None:
            return {"status": "UNVERIFIED_2D", "reason": "missing_die", "samples": []}
        for i in range(sample_count):
            fraction = i / (sample_count - 1)
            ram_z = bottom_ram_z_mm + fraction * (parked_ram_z_mm - bottom_ram_z_mm)
            tooling.set_punch_ram_z(ram_z, stroke_mm=0.0)
            punch = tooling.punch_polygons_machine(arc_segments=24)
            if punch is None:
                return {"status": "UNVERIFIED_2D", "reason": "missing_punch", "samples": []}
            report = check_machine_section(polygons, punch, die, tolerance_mm)
            findings = [
                {"tool": f.tool, "component": f.sheet_component,
                 "status": f.status.value, "reason": f.reason}
                for f in report.findings
                if f.status in (ContactStatus.INTERFERENCE, ContactStatus.UNVERIFIED)
            ]
            samples.append({
                "phase": "BOTTOM" if i == 0 else "RETRACT",
                "fraction": fraction, "ram_z_mm": ram_z,
                "status": report.status.value,
                "findings": findings[:12],
            })
    finally:
        if "original_ram_z" in locals():
            tooling.set_punch_ram_z(original_ram_z, original_ram_stroke)
            tooling.punch_machine_transform = original_punch_tf
    statuses = [s["status"] for s in samples]
    if "INTERFERENCE" in statuses:
        status = "SAMPLED_2D_INTERFERENCE"
    elif "UNVERIFIED" in statuses:
        status = "UNVERIFIED_2D"
    elif "TOUCH" in statuses:
        status = "SAMPLED_2D_TOUCH_OR_CLEAR_NOT_CERTIFIED"
    else:
        status = "SAMPLED_2D_CLEAR_NOT_CERTIFIED"
    return {"status": status, "samples": samples,
            "reason": "fixed_real_folded_section_rising_punch_only"}



def probe_lift_then_withdraw(polygons, punch, die, *,
                             lift_mm=20.0, distance_mm=100.0,
                             direction=1, lift_samples=21,
                             withdrawal_samples=21, tolerance_mm=0.02):
    """Two-segment test: raise the formed section, then move it horizontally.

    All tools remain fixed at the parked configuration. An initial TOUCH with
    the die is permitted in this diagnostic only, but any INTERFERENCE or
    UNVERIFIED prevents claiming sampled clearance. This is not a continuous
    swept-volume collision certificate.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if (not isfinite(lift_mm) or lift_mm <= 0
            or not isfinite(distance_mm) or distance_mm <= 0
            or not 2 <= lift_samples <= 1001
            or not 2 <= withdrawal_samples <= 1001):
        raise ValueError("invalid lift/withdrawal distance or sampling")
    if not polygons:
        return {"status": "UNVERIFIED_2D", "samples": [],
                "reason": "empty_part_section"}
    samples = []
    for phase, count in (("LIFT", lift_samples),
                         ("LATERAL_AFTER_LIFT", withdrawal_samples)):
        for i in range(count):
            if phase == "LIFT":
                z = lift_mm * i / (count - 1)
                ds = 0.0
            else:
                z = lift_mm
                ds = direction * distance_mm * i / (count - 1)
            moved = tuple(
                (name, tuple((float(x) + ds, float(y) + z)
                             for x, y in poly))
                for name, poly in polygons
            )
            report = check_machine_section(moved, punch, die, tolerance_mm)
            problems = [
                {"tool": finding.tool, "component": finding.sheet_component,
                 "status": finding.status.value, "reason": finding.reason}
                for finding in report.findings
                if finding.status in (ContactStatus.INTERFERENCE,
                                      ContactStatus.UNVERIFIED)
            ]
            samples.append({
                "phase": phase, "lift_z_mm": z, "offset_s_mm": ds,
                "status": report.status.value, "findings": problems[:12],
            })
    statuses = {sample["status"] for sample in samples}
    if "INTERFERENCE" in statuses:
        status = "SAMPLED_2D_INTERFERENCE"
    elif "UNVERIFIED" in statuses:
        status = "UNVERIFIED_2D"
    elif "TOUCH" in statuses:
        status = "SAMPLED_2D_TOUCH_OR_CLEAR_NOT_CERTIFIED"
    else:
        status = "SAMPLED_2D_CLEAR_NOT_CERTIFIED"
    return {
        "status": status, "samples": samples,
        "reason": "hypothetical_vertical_lift_then_horizontal_s_translation",
    }


def run_folded_part_cycle(
    doc, *, sequence=("B01", "B02"),
    punch_id=DEFAULT_PUNCH, die_id=DEFAULT_DIE,
    distance_mm=100.0, retract_samples=21, withdrawal_samples=21,
    parked_clearance_mm=20.0, tolerance_mm=0.02,
    lift_mm=20.0, lift_samples=21,
    output_path=None,
):
    """Compute actual post-bend section, sample ram retract and lateral removal.

    Explicitly does not attempt next-bend transfer: reorientation trajectory,
    backgauge placement and frame geometry are presently unavailable.
    """
    started = perf_counter()
    if doc is None:
        raise ValueError("FreeCAD document required")
    if not isfinite(parked_clearance_mm) or parked_clearance_mm <= 0:
        raise ValueError("parked_clearance_mm must be finite and positive")
    unfold = next((o for o in doc.Objects if o.Name.endswith("_Unfold")), None)
    if unfold is None:
        raise ValueError("SheetMetal Unfold is required")
    session = build_part_session(doc, [unfold])
    state = session.fold_state
    bends = tuple(sequence)
    if (not bends or len(bends) != len(set(bends))
            or set(bends) != set(state.bends)):
        raise ValueError("sequence must contain every real bend exactly once")
    lib = ToolLibrary25D()
    tooling = ToolingSetup2D()
    tooling.select_punch(lib.load_tool(punch_id, ToolKind.PUNCH))
    tooling.select_die(lib.load_tool(die_id, ToolKind.DIE))
    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    steps = []
    try:
        state.reset_all()
        for index, bid in enumerate(bends):
            bend = state.bends[bid]
            state.set_angle(bid, float(bend.angle_deg))
            entry = {"bend_id": bid, "completed_bends": list(bends[:index+1])}
            try:
                outer_r = float(bend.bend_zone.outer_radius_mm)
                # First align tools against the folded section; source-derived
                # thickness is required by align_to_cross_section.
                preliminary_pose = WorkpiecePlacement3D.compute_placement(
                    state, bid, desired_midpoint_xm=0.0,
                    support_plane_zm=outer_r,
                    align_bisector=True, align_bend_arc_center=True,
                )
                preliminary_section = compute_machine_cutting_plane_section(
                    state, session.panel_vertices_xy, preliminary_pose,
                    active_bend_id=bid, segments=32,
                )
                tooling.align_to_cross_section(
                    preliminary_section, clearance_mm=parked_clearance_mm,
                    bend_id=bid,
                )
                coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
                    tooling, state, bid
                )
                if coupled.tooling_status != "VERIFIED":
                    raise ValueError(
                        "tooling datums not verified: " + coupled.tooling_status
                    )
                stroke = coupled.stroke_for_angle(abs(float(bend.angle_deg)))
                bottom_ram_z = coupled.ram_z_for_angle(abs(float(bend.angle_deg)))
                parked_ram_z = coupled.contact_z_mm + parked_clearance_mm
                # Use the same stroke-dependent support placement as Stage B,
                # not the pre-bend position used by the old lateral pilot.
                bottom_pose = WorkpiecePlacement3D.compute_placement(
                    state, bid, desired_midpoint_xm=0.0,
                    support_plane_zm=outer_r - stroke,
                    align_bisector=True, align_bend_arc_center=True,
                )
                bottom_section = compute_machine_cutting_plane_section(
                    state, session.panel_vertices_xy, bottom_pose,
                    active_bend_id=bid, segments=32,
                )
                polygons = _parts(bottom_section)
                if not polygons:
                    raise ValueError("no bottom-position part sections")
                entry["section_components"] = [name for name, _ in polygons]
                entry["stroke_mm"] = stroke
                entry["bottom_ram_z_mm"] = bottom_ram_z
                entry["parked_ram_z_mm"] = parked_ram_z
                entry["retract"] = probe_punch_retract(
                    polygons, tooling, bottom_ram_z_mm=bottom_ram_z,
                    parked_ram_z_mm=parked_ram_z,
                    sample_count=retract_samples, tolerance_mm=tolerance_mm,
                )
                tooling.set_punch_ram_z(parked_ram_z)
                punch = tooling.punch_polygons_machine(arc_segments=24)
                die = tooling.die_polygons_machine(arc_segments=24)
                entry["withdrawal"] = {
                    "negative_s": probe_lateral_withdrawal(
                        polygons, punch, die,
                        distance_mm=distance_mm, direction=-1,
                        sample_count=withdrawal_samples,
                        tolerance_mm=tolerance_mm,
                    ),
                    "positive_s": probe_lateral_withdrawal(
                        polygons, punch, die,
                        distance_mm=distance_mm, direction=1,
                        sample_count=withdrawal_samples,
                        tolerance_mm=tolerance_mm,
                    ),
                }
                entry["lift_then_withdrawal"] = {
                    "negative_s": probe_lift_then_withdraw(
                        polygons, punch, die,
                        lift_mm=lift_mm, distance_mm=distance_mm,
                        direction=-1, lift_samples=lift_samples,
                        withdrawal_samples=withdrawal_samples,
                        tolerance_mm=tolerance_mm,
                    ),
                    "positive_s": probe_lift_then_withdraw(
                        polygons, punch, die,
                        lift_mm=lift_mm, distance_mm=distance_mm,
                        direction=1, lift_samples=lift_samples,
                        withdrawal_samples=withdrawal_samples,
                        tolerance_mm=tolerance_mm,
                    ),
                }
                entry["transfer"] = {
                    "status": "UNVERIFIED_2D",
                    "reason": ("next_bend_pose_change_not_swept"
                               if index < len(bends)-1
                               else "final_part_removal_not_3d_validated"),
                }
                entry["status"] = "SAMPLED_2D_POST_BEND_CYCLE_NOT_CERTIFIED"
            except Exception as exc:
                entry["status"] = "UNVERIFIED_2D"
                entry["reason"] = type(exc).__name__ + ": " + str(exc)
            steps.append(entry)
    finally:
        state.angles.clear()
        state.angles.update(original_angles)
        state.transforms.clear()
        state.transforms.update(original_transforms)
    payload = {
        "model": doc.Label, "sequence": list(bends),
        "punch": punch_id, "die": die_id,
        "retract_samples": retract_samples,
        "withdrawal_samples": withdrawal_samples,
        "distance_mm": distance_mm,
        "lift_mm": lift_mm, "lift_samples": lift_samples,
        "evidence": "POST_BEND_MACHINE_SECTION_SAMPLES_ONLY_NOT_3D_CERTIFIED",
        "limitations": [
            "Stationary bent section and ram-only upward motion: no springback",
            "Single central section; discrete samples do not certify continuous path",
            "Horizontal +s/-s withdrawal only after ram reaches parked height",
            "Optional lift then lateral withdraw is a hypothetical 2D trajectory, not a planned machine motion",
            "TRANSFER rotation/flip between machine setups NOT evaluated",
            "No machine frame, backgauge, clamps, punch/die end faces or 3D sweep",
        ],
        "steps": steps, "elapsed_s": perf_counter() - started,
    }
    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return payload


def print_folded_part_cycle(payload):
    print("REAL FOLDED PART / POST-BEND CYCLE 2D PREVIEW")
    print("MODEL:", payload["model"], "SEQUENCE:", " -> ".join(payload["sequence"]))
    for step in payload["steps"]:
        print(" ", step["bend_id"], step["status"])
        if step.get("reason"):
            print("   REASON:", step["reason"])
        retract = step.get("retract")
        if retract:
            print("   BOTTOM/RETRACT:", retract["status"],
                  "INTERFERENCE:", sum(s["status"] == "INTERFERENCE" for s in retract["samples"]))
            for sample in retract["samples"]:
                if sample["findings"]:
                    print("    FIRST ISSUE:", sample["phase"],
                          "ram_z_mm=", round(sample["ram_z_mm"], 3),
                          sample["findings"][:2])
                    break
        for direction, path in step.get("withdrawal", {}).items():
            print("   WITHDRAWAL", direction, path["status"],
                  "INTERFERENCE:", sum(s["status"] == "INTERFERENCE" for s in path["samples"]))
        for direction, path in step.get("lift_then_withdrawal", {}).items():
            problems = [sample for sample in path["samples"]
                        if sample["status"] in ("INTERFERENCE", "UNVERIFIED")]
            print("   LIFT+WITHDRAW", direction, path["status"],
                  "PROBLEMS:", len(problems))
            if problems:
                print("    FIRST:", problems[0]["phase"],
                      "lift_z_mm=", round(problems[0]["lift_z_mm"], 3),
                      "s_mm=", round(problems[0]["offset_s_mm"], 3),
                      problems[0]["findings"][:2])
        if "transfer" in step:
            print("   TRANSFER:", step["transfer"]["status"],
                  step["transfer"]["reason"])
    print("LIMIT: Not a verified physical machine sequence")
