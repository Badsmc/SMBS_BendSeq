"""Experimental planar withdrawal probe using the ACTUAL folded FreeCAD part section.

The part section is extracted from the production FoldState at each completed
bend. We translate that real section along an explicit machine-space s path
relative to the selected punch and die. This is a read-only, sampled 2D
obstruction probe, NOT a 3D collision certificate, backgauge check, or
automatically optimized operator handling strategy.
"""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from time import perf_counter

from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactStatus, check_machine_section,
)
from smbs_core.kinematics2d.machine_section_3d import compute_machine_cutting_plane_section
from smbs_core.kinematics2d.tooling_25d import ToolKind, ToolLibrary25D, ToolingSetup2D
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D

DEFAULT_PUNCH = "PUNCH_AP_1017_88°_H104,65_R0,8"
DEFAULT_DIE = "T-DIE_AT_3015_88°_V12_H80"


def _parts(section):
    result = []
    for key, name in (
        ("fixed_polygon", "active_fixed"),
        ("moving_polygon", "active_moving"),
        ("zone_polygon", "active_bend_zone"),
    ):
        if section.get(key):
            result.append((name, tuple(section[key])))
    for key in ("other_panel_polygons", "other_zone_polygons"):
        result.extend((str(name), tuple(poly))
                      for name, poly in section.get(key, ()) if poly)
    return tuple(result)


def probe_lateral_withdrawal(
    polygons, punch, die, *, distance_mm: float = 100.0,
    direction: int = 1, sample_count: int = 21,
    tolerance_mm: float = 0.02,
):
    """Sample translation of a single fixed folded section relative to tooling.

    Includes the initial parked-punch position as a sample. TOUCH is distinct
    from clearance; INTERFERENCE demonstrates a sampled planar obstruction.
    Other directions/rotation paths and 3D end faces are NOT explored.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if sample_count < 2 or sample_count > 1001:
        raise ValueError("sample_count must be in [2,1001]")
    if not isfinite(distance_mm) or distance_mm <= 0:
        raise ValueError("distance_mm must be positive finite")
    if not polygons:
        return {
            "status": "UNVERIFIED_NO_PART_SECTION", "samples": [],
            "reason": "no_real_folded_section_components",
        }
    samples = []
    for i in range(sample_count):
        fraction = i / (sample_count - 1)
        offset = direction * distance_mm * fraction
        moved = tuple((
            name, tuple((float(s) + offset, float(z)) for s, z in polygon)
        ) for name, polygon in polygons)
        report = check_machine_section(moved, punch, die, tolerance_mm)
        findings = tuple(
            {"tool": f.tool, "component": f.sheet_component,
             "status": f.status.value, "reason": f.reason}
            for f in report.findings
            if f.status in (ContactStatus.INTERFERENCE, ContactStatus.UNVERIFIED)
        )
        samples.append({
            "fraction": fraction, "offset_s_mm": offset,
            "status": report.status.value, "findings": findings[:12],
        })
    statuses = [item["status"] for item in samples]
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
        "reason": "sampled_horizontal_machine_s_translation_only",
    }


def run_folded_part_withdrawal(
    doc, *, sequence=("B01", "B02"),
    punch_id=DEFAULT_PUNCH, die_id=DEFAULT_DIE,
    distance_mm=100.0, sample_count=21,
    parked_clearance_mm=20.0, tolerance_mm=0.02,
    output_path=None,
):
    """Probe both lateral directions following each bend on a real folded part.

    Automatically uses fold geometry extracted from the open SheetMetal Unfold.
    Leaves document, FoldState and selected tool catalog unchanged. If setup
    or section extraction fails, emits UNVERIFIED and diagnostic reason.
    """
    started = perf_counter()
    if doc is None:
        raise ValueError("FreeCAD document required")
    if not isfinite(parked_clearance_mm) or parked_clearance_mm <= 0:
        raise ValueError("parked_clearance_mm must be positive finite")
    unfold = next((o for o in doc.Objects if o.Name.endswith("_Unfold")), None)
    if unfold is None:
        raise ValueError("SheetMetal Unfold is required")
    session = build_part_session(doc, [unfold])
    state = session.fold_state
    bends = tuple(sequence)
    if not bends or len(bends) != len(set(bends)) or any(b not in state.bends for b in bends):
        raise ValueError("sequence must contain unique real FoldState bend ids")
    lib = ToolLibrary25D()
    tooling = ToolingSetup2D()
    tooling.select_punch(lib.load_tool(punch_id, ToolKind.PUNCH))
    tooling.select_die(lib.load_tool(die_id, ToolKind.DIE))
    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    results = []
    try:
        state.reset_all()
        for bid in bends:
            bend = state.bends[bid]
            state.set_angle(bid, float(bend.angle_deg))
            entry = {"bend_id": bid, "completed_bends": list(b for b in bends[:bends.index(bid)+1])}
            try:
                outer_r = float(getattr(bend.bend_zone, "outer_radius_mm", 0.0))
                placement = WorkpiecePlacement3D.compute_placement(
                    state, bid, desired_midpoint_xm=0.0,
                    support_plane_zm=outer_r, align_bisector=True,
                    align_bend_arc_center=True,
                )
                section = compute_machine_cutting_plane_section(
                    state, session.panel_vertices_xy, placement,
                    active_bend_id=bid, segments=32,
                )
                polygons = _parts(section)
                if not polygons:
                    raise ValueError("empty part cross section")
                tooling.align_to_cross_section(
                    section, clearance_mm=parked_clearance_mm,
                    bend_id=bid,
                )
                # Keep the punch in the explicitly aligned clearance pose.
                # Do NOT infer the actual press-ram withdrawal from this pose.
                punch = tooling.punch_polygons_machine(arc_segments=24)
                die = tooling.die_polygons_machine(arc_segments=24)
                if punch is None or die is None:
                    raise ValueError("missing tool contours")
                entry["section_components"] = [name for name, _ in polygons]
                entry["paths"] = {
                    "negative_s": probe_lateral_withdrawal(
                        polygons, punch, die,
                        distance_mm=distance_mm, direction=-1,
                        sample_count=sample_count, tolerance_mm=tolerance_mm,
                    ),
                    "positive_s": probe_lateral_withdrawal(
                        polygons, punch, die,
                        distance_mm=distance_mm, direction=1,
                        sample_count=sample_count, tolerance_mm=tolerance_mm,
                    ),
                }
                entry["status"] = "SAMPLED_2D_LATERAL_PATHS_ONLY"
            except Exception as exc:
                entry["status"] = "UNVERIFIED_2D"
                entry["reason"] = type(exc).__name__ + ": " + str(exc)
            results.append(entry)
    finally:
        state.angles.clear()
        state.angles.update(original_angles)
        state.transforms.clear()
        state.transforms.update(original_transforms)
    payload = {
        "model": doc.Label,
        "sequence": list(bends),
        "punch": punch_id, "die": die_id,
        "distance_mm": distance_mm,
        "sample_count": sample_count,
        "parked_clearance_mm": parked_clearance_mm,
        "evidence": "FOLDED_PART_SAMPLED_2D_SECTION_NOT_3D_CERTIFIED",
        "limitations": [
            "Only one central X_m cutting-plane section per pose",
            "Only translations along machine s (+ and -); no rotations or alternative transfers",
            "The punch is parked at alignment clearance; actual retract stroke is not reconstructed",
            "No backgauge, frame, clamps, tool end faces, or continuous swept volume",
            "A TOUCH or CLEAR path is NOT a physically certified extraction",
        ],
        "steps": results,
        "elapsed_s": perf_counter() - started,
    }
    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return payload


def print_folded_part_withdrawal(report):
    print("REAL FOLDED SECTION / SAMPLED LATERAL WITHDRAWAL")
    print("MODEL:", report["model"], "SEQUENCE:", " -> ".join(report["sequence"]))
    for step in report["steps"]:
        print(" ", step["bend_id"], ":", step["status"])
        if step.get("reason"):
            print("    REASON:", step["reason"])
        for direction, path in step.get("paths", {}).items():
            print("   ", direction, path["status"],
                  "INTERFERENCE SAMPLES:",
                  sum(x["status"] == "INTERFERENCE" for x in path["samples"]),
                  "UNKNOWN SAMPLES:",
                  sum(x["status"] == "UNVERIFIED" for x in path["samples"]))
            for sample in path["samples"]:
                if sample["findings"]:
                    print("     offset_s_mm", round(sample["offset_s_mm"], 2),
                          sample["findings"][:2])
                    break
    print("LIMIT: NOT a real machine extraction certificate")
