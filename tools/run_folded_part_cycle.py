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
from smbs_core.kinematics2d.swept_translation_2d import screen_lift_then_withdraw_swept
from tools.run_folded_part_withdrawal import (
    _parts, probe_lateral_withdrawal, DEFAULT_PUNCH, DEFAULT_DIE,
)


def probe_punch_retract(polygons, tooling, *,
                        bottom_ram_z_mm: float, parked_ram_z_mm: float,
                        sample_count: int = 21, tolerance_mm: float = 0.02,
                        deadline_monotonic=None, stop_on_obstruction=False):
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
            if deadline_monotonic is not None and perf_counter() >= deadline_monotonic:
                return {"status": "UNVERIFIED_SEARCH_LIMIT_REACHED", "samples": samples,
                        "reason": "deadline_during_retract"}
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
            if stop_on_obstruction and (report.status in (ContactStatus.INTERFERENCE, ContactStatus.UNVERIFIED)
                    or (i > 0 and report.status is not ContactStatus.CLEAR)):
                break
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
                             withdrawal_samples=21, tolerance_mm=0.02,
                             deadline_monotonic=None, stop_on_obstruction=False,
                             cached_lift_samples=None):
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
    samples = list(cached_lift_samples or ())
    for phase, count in (("LIFT", lift_samples),
                         ("LATERAL_AFTER_LIFT", withdrawal_samples)):
        if phase == "LIFT" and cached_lift_samples is not None:
            continue
        for i in range(count):
            if deadline_monotonic is not None and perf_counter() >= deadline_monotonic:
                return {"status": "UNVERIFIED_SEARCH_LIMIT_REACHED", "samples": samples,
                        "reason": "deadline_during_" + phase}
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
            if stop_on_obstruction and report.status is not ContactStatus.CLEAR:
                break
        if stop_on_obstruction and samples and samples[-1]["status"] != "CLEAR":
            break
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



def search_sampled_withdrawal_paths(
    polygons, tooling, *, bottom_ram_z_mm, contact_z_mm,
    parked_clearances_mm=(20.0, 35.0, 50.0),
    lift_heights_mm=(2.0, 5.0, 8.0, 12.0, 16.0, 20.0),
    distance_mm=100.0, retract_samples=21,
    lift_samples=21, withdrawal_samples=21,
    tolerance_mm=0.02, max_search_time_s=60.0,
    verify_swept_paths=False,
):
    """Bounded search, fail-closed on deadline and on obstructed partial paths.

    A verified CLEAR candidate has every sampled pose checked. Any incomplete
    path remains UNVERIFIED; no geometric partial result is promoted to CLEAR.
    """
    clearances = tuple(float(c) for c in parked_clearances_mm)
    heights = tuple(float(h) for h in lift_heights_mm)
    if (not clearances or not heights
            or len(clearances) > 20 or len(heights) > 50
            or any(not isfinite(c) or c <= 0 for c in clearances)
            or any(not isfinite(h) or h <= 0 for h in heights)
            or not isfinite(max_search_time_s) or max_search_time_s <= 0):
        raise ValueError("invalid path search parameters")
    if not polygons:
        return {"status": "UNVERIFIED_NO_PART_SECTION", "candidates": [],
                "attempts": [], "evaluated_paths": 0}
    start = perf_counter()
    deadline = start + max_search_time_s
    attempts = []
    candidates = []
    stopped = False
    pose_evaluations = 0
    original = (float(tooling.ram_z_mm), float(tooling.ram_stroke_mm),
                tooling.punch_machine_transform)
    try:
        for clearance in sorted(set(clearances)):
            if perf_counter() >= deadline:
                stopped = True
                break
            parked_z = contact_z_mm + clearance
            if parked_z < bottom_ram_z_mm:
                attempts.append({"parked_clearance_mm": clearance,
                                 "status": "INVALID_PARKED_BELOW_BOTTOM"})
                continue
            retract = probe_punch_retract(
                polygons, tooling,
                bottom_ram_z_mm=bottom_ram_z_mm, parked_ram_z_mm=parked_z,
                sample_count=retract_samples, tolerance_mm=tolerance_mm,
                deadline_monotonic=deadline, stop_on_obstruction=True,
            )
            pose_evaluations += len(retract["samples"])
            if retract["status"] == "UNVERIFIED_SEARCH_LIMIT_REACHED":
                stopped = True
                break
            bad = next((x for x in retract["samples"]
                        if x["status"] in ("INTERFERENCE", "UNVERIFIED")
                        or (x["phase"] != "BOTTOM" and x["status"] != "CLEAR")), None)
            if bad or len(retract["samples"]) != retract_samples:
                attempts.append({"parked_clearance_mm": clearance,
                                 "status": "RETRACT_NOT_CLEAR",
                                 "first_problem": bad})
                continue
            tooling.set_punch_ram_z(parked_z)
            punch = tooling.punch_polygons_machine(arc_segments=24)
            die = tooling.die_polygons_machine(arc_segments=24)
            if punch is None or die is None:
                attempts.append({"parked_clearance_mm": clearance,
                                 "status": "UNVERIFIED_MISSING_TOOL"})
                continue
            for lift in sorted(set(heights)):
                if perf_counter() >= deadline:
                    stopped = True
                    break
                # Compute lift once for each height; both lateral paths share it.
                lift_result = probe_lift_then_withdraw(
                    polygons, punch, die, lift_mm=lift, distance_mm=distance_mm,
                    direction=-1, lift_samples=lift_samples,
                    withdrawal_samples=withdrawal_samples,
                    tolerance_mm=tolerance_mm, deadline_monotonic=deadline,
                    stop_on_obstruction=True,
                )
                pose_evaluations += len(lift_result["samples"])
                prefix = [x for x in lift_result["samples"] if x["phase"] == "LIFT"]
                for direction in (-1, 1):
                    if perf_counter() >= deadline:
                        stopped = True
                        break
                    if direction == -1:
                        path = lift_result
                    elif len(prefix) != lift_samples or any(x["status"] != "CLEAR" for x in prefix):
                        path = {"status": "UNVERIFIED_2D",
                                "samples": list(prefix),
                                "reason": "lift_failed_for_both_directions"}
                    else:
                        path = probe_lift_then_withdraw(
                            polygons, punch, die, lift_mm=lift,
                            distance_mm=distance_mm, direction=direction,
                            lift_samples=lift_samples,
                            withdrawal_samples=withdrawal_samples,
                            tolerance_mm=tolerance_mm, deadline_monotonic=deadline,
                            stop_on_obstruction=True, cached_lift_samples=prefix,
                        )
                        pose_evaluations += len(path["samples"]) - len(prefix)
                    if path["status"] == "UNVERIFIED_SEARCH_LIMIT_REACHED":
                        stopped = True
                    complete = (len(path["samples"]) == lift_samples + withdrawal_samples)
                    all_clear = (complete and all(
                        x["status"] == "CLEAR" for x in path["samples"]
                    ))
                    first_problem = next(
                        (x for x in path["samples"] if x["status"] != "CLEAR"), None
                    )
                    item = {
                        "parked_clearance_mm": clearance,
                        "parked_ram_z_mm": parked_z,
                        "lift_mm": lift,
                        "direction": "negative_s" if direction < 0 else "positive_s",
                        "status": ("SAMPLED_2D_CLEAR_NOT_CERTIFIED" if all_clear
                                   else "UNVERIFIED_SEARCH_LIMIT_REACHED" if stopped
                                   else path["status"]),
                        "first_problem": first_problem,
                        "sample_count": len(path["samples"]),
                        "complete": complete,
                    }
                    attempts.append(item)
                    if all_clear:
                        candidates.append(item)
                    if stopped:
                        break
                if stopped:
                    break
            if stopped:
                break
    finally:
        tooling.set_punch_ram_z(original[0], original[1])
        tooling.punch_machine_transform = original[2]
    candidates.sort(key=lambda x: (x["lift_mm"], x["parked_clearance_mm"], x["direction"]))
    # Sweep verification uses each candidate's REAL parked punch transform.
    # A failed or ambiguous sweep is preserved in the sampled candidates list,
    # but is never admitted to sweep_screened_candidates.
    sweep_candidates = []
    if verify_swept_paths and candidates:
        try:
            for candidate in candidates:
                if perf_counter() >= deadline:
                    stopped = True
                    candidate["sweep_screening"] = {
                        "status": "UNVERIFIED_SEARCH_LIMIT_REACHED",
                        "reason": "deadline_before_sweep",
                    }
                    continue
                tooling.set_punch_ram_z(candidate["parked_ram_z_mm"])
                punch = tooling.punch_polygons_machine(arc_segments=24)
                die = tooling.die_polygons_machine(arc_segments=24)
                direction = -1 if candidate["direction"] == "negative_s" else 1
                sweep = screen_lift_then_withdraw_swept(
                    polygons, punch, die, lift_mm=candidate["lift_mm"],
                    distance_mm=distance_mm, direction=direction,
                    tolerance_mm=tolerance_mm, deadline_monotonic=deadline,
                )
                candidate["sweep_screening"] = sweep
                if sweep["status"] == "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED":
                    sweep_candidates.append(candidate)
                elif sweep["status"] == "UNVERIFIED_SEARCH_LIMIT_REACHED":
                    stopped = True
        finally:
            tooling.set_punch_ram_z(original[0], original[1])
            tooling.punch_machine_transform = original[2]

    return {
        "status": ("SAMPLED_2D_PATH_CANDIDATES_NOT_CERTIFIED" if candidates
                   else "UNVERIFIED_SEARCH_LIMIT_REACHED" if stopped
                   else "UNVERIFIED_NO_SAMPLED_PATH"),
        "candidates": candidates,
        "sweep_screened_candidates": sweep_candidates,
        "sweep_verification_requested": bool(verify_swept_paths),
        "attempts": attempts,
        "evaluated_paths": sum("lift_mm" in x for x in attempts),
        "evaluated_poses": pose_evaluations,
        "search_limit_reached": stopped,
        "elapsed_s": perf_counter() - start,
        "limits": {
            "max_search_time_s": max_search_time_s,
            "parked_clearances_mm": sorted(set(clearances)),
            "lift_heights_mm": sorted(set(heights)),
        },
        "disclaimer": "Sampled 2D paths only; transfer and 3D not verified.",
    }


def run_folded_part_cycle(
    doc, *, sequence=("B01", "B02"),
    punch_id=DEFAULT_PUNCH, die_id=DEFAULT_DIE,
    distance_mm=100.0, retract_samples=21, withdrawal_samples=21,
    parked_clearance_mm=20.0, tolerance_mm=0.02,
    lift_mm=20.0, lift_samples=21,
    search_paths=False,
    parked_clearances_mm=(20.0, 35.0, 50.0),
    lift_heights_mm=(2.0, 5.0, 8.0, 12.0, 16.0, 20.0),
    path_search_time_s=60.0,
    verify_swept_paths=False,
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
                if search_paths:
                    entry["path_search"] = search_sampled_withdrawal_paths(
                        polygons, tooling,
                        bottom_ram_z_mm=bottom_ram_z,
                        contact_z_mm=coupled.contact_z_mm,
                        parked_clearances_mm=parked_clearances_mm,
                        lift_heights_mm=lift_heights_mm,
                        distance_mm=distance_mm,
                        retract_samples=retract_samples,
                        lift_samples=lift_samples,
                        withdrawal_samples=withdrawal_samples,
                        tolerance_mm=tolerance_mm,
                        max_search_time_s=path_search_time_s,
                        verify_swept_paths=verify_swept_paths,
                    )
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
        "search_paths": bool(search_paths),
        "verify_swept_paths": bool(verify_swept_paths),
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
        if "path_search" in step:
            search = step["path_search"]
            print("   PATH SEARCH:", search["status"],
                  "CANDIDATES:", len(search["candidates"]),
                  "EVALUATED:", search["evaluated_paths"],
                  "LIMIT:", search["search_limit_reached"])
            if search.get("sweep_verification_requested"):
                print("    SWEPT 2D SCREENED:",
                      len(search.get("sweep_screened_candidates", ())),
                      "of", len(search["candidates"]))
                for candidate in search["candidates"]:
                    verification = candidate.get("sweep_screening", {})
                    print("     ", candidate["direction"],
                          "lift=", candidate["lift_mm"],
                          "punch_clearance=", candidate["parked_clearance_mm"],
                          verification.get("status", "NOT_EVALUATED"),
                          verification.get("first_problem", {}).get("tool", "")
                          if isinstance(verification.get("first_problem"), dict)
                          else "")

            for candidate in search["candidates"][:3]:
                print("    CANDIDATE:",
                      candidate["direction"],
                      "lift_mm=", candidate["lift_mm"],
                      "park_clearance_mm=", candidate["parked_clearance_mm"])
        if "transfer" in step:
            print("   TRANSFER:", step["transfer"]["status"],
                  step["transfer"]["reason"])
    print("LIMIT: Not a verified physical machine sequence")
