"""tools/reproduce_box_last_bend_collisions.py

Step 1: Reproduce blocking poses for candidate last bends on box.100out-50in-(ok).FCStd
under authoritative physics and export FreeCAD diagnostic scenes with intersections.
"""
from __future__ import annotations

import json
import math
import os
import sys
import zipfile
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

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.tooling_25d import ToolKind
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_punch,
    load_native_die,
    set_punch_ram_z,
)
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
    _panel_build_geometry_signature,
    _die_geometry_signature,
    _punch_geometry_signature,
)
from smbs_core.kinematics2d.native_collision_3d import (
    classify_punch_overlap_zones,
    PunchCollisionZoneClassification,
)


def write_gui_document_xml(
    fcstd_path: Path,
    object_specs: List[Tuple[str, int, int]],  # (name, color_rgba_int, transparency)
) -> None:
    vp_blocks = []
    for obj_name, color_val, transp in object_specs:
        vp_blocks.append(f"""        <ViewProvider name="{obj_name}">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="{color_val}"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="{transp}"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>""")
    vp_xml = "\n".join(vp_blocks)
    gui_xml = f"""<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1" HasExpansion="1">
    <ViewProviderData Count="{len(object_specs)}">
{vp_xml}
    </ViewProviderData>
    <CameraSettings>
        <ViewCamera value="OrthographicCamera"/>
    </CameraSettings>
</Document>
"""
    with zipfile.ZipFile(fcstd_path, "a") as zf:
        zf.writestr("GuiDocument.xml", gui_xml)


def analyze_collision_location(
    c_punch_solid: Any,
    c_die_solid: Any,
    punch_shape: Any,
    die_shape: Any,
    bend_length_mm: float,
) -> Dict[str, Any]:
    vol_p = float(getattr(c_punch_solid, "Volume", 0.0))
    vol_d = float(getattr(c_die_solid, "Volume", 0.0))

    location_desc = []
    punch_ez_info = None

    if vol_p > 1e-4:
        # Check against end zones (using standard 15mm or 20% of tool length)
        ez_len = min(15.0, bend_length_mm * 0.25)
        ez_res = classify_punch_overlap_zones(
            c_punch_solid, punch_shape, end_zone_length_mm=ez_len
        )
        punch_ez_info = {
            "classification": ez_res.classification.value,
            "end_zone_len_mm": ez_len,
            "total_vol_mm3": ez_res.total_overlap_volume_mm3,
            "end_zone_vol_mm3": ez_res.end_zone_overlap_volume_mm3,
            "central_vol_mm3": ez_res.central_zone_overlap_volume_mm3,
            "affected_sides": list(ez_res.affected_sides),
            "punch_span": ez_res.punch_span_xm,
        }
        if ez_res.classification == PunchCollisionZoneClassification.END_ZONE_ONLY:
            location_desc.append("PUNCH_END_ZONE_ONLY")
        elif ez_res.classification == PunchCollisionZoneClassification.CENTRAL_ZONE:
            location_desc.append("PUNCH_CENTRAL_BODY")
        elif ez_res.classification == PunchCollisionZoneClassification.MIXED_CENTRAL_AND_END_ZONE:
            location_desc.append("PUNCH_MIXED_CENTRAL_AND_END_ZONE")
        else:
            location_desc.append("PUNCH_COLLISION")

    if vol_d > 1e-4:
        location_desc.append("DIE_BODY")

    return {
        "punch_overlap_volume_mm3": vol_p,
        "die_overlap_volume_mm3": vol_d,
        "collision_location": " + ".join(location_desc) if location_desc else "CLEAR",
        "punch_end_zone_analysis": punch_ez_info,
    }


def main():
    fixture_name = "box.100out-50in-(ok).FCStd"
    fixture_path = demo_part_path(fixture_name)
    out_dir = ROOT / "reports" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_src = App.openDocument(str(fixture_path), True)
    unfold = next(o for o in doc_src.Objects if o.Name.endswith("_Unfold"))
    session = build_part_session(doc_src, [unfold])

    panel = _PreviewPanel()
    panel.session = session
    p_name = "PUNCH_AP_1017_88°_H104,65_R0,8"
    d_name = "T-DIE_AT_3015_88°_V12_H80"
    prof_p = panel.tool_library.load_tool(p_name, ToolKind.PUNCH)
    prof_d = panel.tool_library.load_tool(d_name, ToolKind.DIE)
    panel.select_punch(prof_p)
    panel.select_die(prof_d)
    panel.align_tooling()

    all_bends = list(session.fold_state.bends.keys())
    target_angles = {bid: float(session.fold_state.bends[bid].angle_deg) for bid in all_bends}

    # Reproduction targets per prompt:
    # B01 & B03: BENDING (u=1.0)
    # B02 & B04: APPROACH (u=0.75)
    # B05 & B07: CONTACT / Initial pose
    # B06 & B08: CONTACT / Initial pose
    test_cases = [
        {"bend_id": "B01", "phase": "BENDING", "u": 1.0, "shift_xm": 0.0},
        {"bend_id": "B03", "phase": "BENDING", "u": 1.0, "shift_xm": 0.0},
        {"bend_id": "B02", "phase": "APPROACH", "u": 0.75, "shift_xm": 0.0},
        {"bend_id": "B04", "phase": "APPROACH", "u": 0.75, "shift_xm": 0.0},
        {"bend_id": "B05", "phase": "CONTACT", "u": 0.0, "shift_xm": 0.0},
        {"bend_id": "B06", "phase": "CONTACT", "u": 0.0, "shift_xm": 0.0},
        {"bend_id": "B07", "phase": "CONTACT", "u": 0.0, "shift_xm": 0.0},
        {"bend_id": "B08", "phase": "CONTACT", "u": 0.0, "shift_xm": 0.0},
    ]

    results = []

    for tc in test_cases:
        cand_id = tc["bend_id"]
        phase = tc["phase"]
        u = float(tc["u"])
        shift_xm = float(tc["shift_xm"])

        # Prior 7 bends at target angles; candidate at intermediate angle
        prior = {bid: target_angles[bid] for bid in all_bends if bid != cand_id}
        active_bend = session.fold_state.bends[cand_id]
        bz = active_bend.bend_zone
        bend_len = math.dist(active_bend.axis_xyz[0], active_bend.axis_xyz[1])
        target_angle = target_angles[cand_id]
        sign = 1.0 if target_angle >= 0.0 else -1.0
        abs_target_angle = abs(target_angle)

        # Load matched tools
        punch = load_native_punch(prof_p.source_file, ram_z_mm=50.0, virtual_length_mm=bend_len)
        die = load_native_die(prof_d.source_file, virtual_length_mm=bend_len)

        coupled = CoupledAirBendingKinematics(
            die_opening_v_mm=die.opening_v_mm if hasattr(die, "opening_v_mm") else 12.0,
            die_shoulder_radius_mm=die.shoulder_radius_mm if hasattr(die, "shoulder_radius_mm") else 2.0,
            die_angle_deg=die.included_angle_deg if hasattr(die, "included_angle_deg") else 88.0,
            die_v_depth_mm=die.v_depth_mm if hasattr(die, "v_depth_mm") else 10.0,
            punch_tip_radius_mm=punch.tip_radius_mm if hasattr(punch, "tip_radius_mm") else 0.8,
            punch_included_angle_deg=punch.included_angle_deg if hasattr(punch, "included_angle_deg") else 88.0,
            sheet_thickness_mm=bz.thickness_mm,
            sheet_inner_radius_mm=bz.inner_radius_mm,
            die_top_z_mm=0.0,
        )

        if phase == "BENDING":
            theta = u * abs_target_angle
            signed_angle = sign * theta
            ram_z = coupled.ram_z_for_angle(theta)
            support_z = -coupled.stroke_for_angle(theta) + bz.outer_radius_mm
        elif phase == "APPROACH":
            signed_angle = 0.0
            ram_z = 50.0 + u * (coupled.contact_z_mm - 50.0)
            support_z = bz.outer_radius_mm
        elif phase == "CONTACT":
            signed_angle = 0.0
            ram_z = coupled.contact_z_mm
            support_z = bz.outer_radius_mm
        else:
            raise ValueError(f"Unknown phase {phase}")

        # Update punch to exact ram_z
        punch = set_punch_ram_z(punch, ram_z)

        # Set fold state
        session.fold_state.reset_all()
        for bid, ang in prior.items():
            session.fold_state.set_angle(bid, ang)
        session.fold_state.set_angle(cand_id, signed_angle)

        placement = WorkpiecePlacement3D.compute_placement(
            session.fold_state,
            cand_id,
            support_plane_zm=support_z,
            align_bisector=True,
            align_bend_arc_center=True,
            desired_midpoint_xm=shift_xm,
        )

        wp_solid = build_multi_bend_sheet_solid(
            session.fold_state,
            session.panel_build,
            segments=64,
        ).shape.copy()

        mat_wp = App.Matrix(
            placement.rotation[0][0], placement.rotation[0][1], placement.rotation[0][2], placement.translation[0],
            placement.rotation[1][0], placement.rotation[1][1], placement.rotation[1][2], placement.translation[1],
            placement.rotation[2][0], placement.rotation[2][1], placement.rotation[2][2], placement.translation[2],
            0.0, 0.0, 0.0, 1.0,
        )
        wp_solid.transformShape(mat_wp)

        p_step = set_punch_ram_z(punch, ram_z)
        p_solid = p_step.machine_shape.copy()
        d_solid = die.machine_shape.copy()

        c_punch = wp_solid.common(p_solid)
        c_die = wp_solid.common(d_solid)

        analysis = analyze_collision_location(c_punch, c_die, p_solid, d_solid, bend_len)

        # Save diagnostic scene
        scene_name = f"{cand_id}_{phase}_u{str(u).replace('.', '')}_scene"
        scene_fcstd = out_dir / f"{scene_name}.FCStd"

        diag_doc = App.newDocument(scene_name)
        o_wp = diag_doc.addObject("Part::Feature", f"Workpiece_{cand_id}_prior7folded")
        o_wp.Shape = wp_solid

        o_p = diag_doc.addObject("Part::Feature", f"Punch_{p_name[:15]}_ramZ_{ram_z:.2f}")
        o_p.Shape = p_solid

        o_d = diag_doc.addObject("Part::Feature", f"Die_{d_name[:15]}")
        o_d.Shape = d_solid

        obj_specs = [
            (o_wp.Name, 3000673023, 40),   # grey, semi-trans
            (o_p.Name, 1296911871, 30),    # blue
            (o_d.Name, 1717987071, 30),    # dark grey
        ]

        if analysis["punch_overlap_volume_mm3"] > 1e-4:
            o_cp = diag_doc.addObject("Part::Feature", f"Collision_Punch_{cand_id}_{analysis['punch_overlap_volume_mm3']:.2f}mm3")
            o_cp.Shape = c_punch
            obj_specs.append((o_cp.Name, 4278190335, 0))  # Bright red

        if analysis["die_overlap_volume_mm3"] > 1e-4:
            o_cd = diag_doc.addObject("Part::Feature", f"Collision_Die_{cand_id}_{analysis['die_overlap_volume_mm3']:.2f}mm3")
            o_cd.Shape = c_die
            obj_specs.append((o_cd.Name, 4278255615, 0))  # Orange/magenta

        diag_doc.recompute()
        diag_doc.saveAs(str(scene_fcstd))
        App.closeDocument(diag_doc.Name)

        write_gui_document_xml(scene_fcstd, obj_specs)

        rec = {
            "bend_id": cand_id,
            "phase": phase,
            "path_u": u,
            "signed_angle_deg": signed_angle,
            "target_angle_deg": target_angle,
            "ram_z_mm": ram_z,
            "support_z_mm": support_z,
            "shift_xm_mm": shift_xm,
            "bend_length_mm": bend_len,
            "punch_length_mm": bend_len,
            "die_length_mm": bend_len,
            "punch_overlap_volume_mm3": analysis["punch_overlap_volume_mm3"],
            "die_overlap_volume_mm3": analysis["die_overlap_volume_mm3"],
            "collision_location": analysis["collision_location"],
            "punch_end_zone_analysis": analysis["punch_end_zone_analysis"],
            "diagnostic_scene_fcstd": str(scene_fcstd.relative_to(ROOT)),
        }
        results.append(rec)
        print(
            f"[{cand_id}] {phase} (u={u}): ram_z={ram_z:.2f}mm, "
            f"punch_vol={analysis['punch_overlap_volume_mm3']:.2f}mm3, "
            f"die_vol={analysis['die_overlap_volume_mm3']:.2f}mm3, "
            f"loc={analysis['collision_location']} -> saved {scene_fcstd.name}",
            flush=True,
        )

    out_json = out_dir / "box_blocking_poses_reproduction.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nAll 8 blocking poses reproduced and dumped to: {out_json}")


if __name__ == "__main__":
    main()
