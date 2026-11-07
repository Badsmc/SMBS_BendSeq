"""Generate diagnostic FreeCAD scenes for Candidate Tooling & Workpiece inspection at B02 with s = +2.4 mm.

Preserves existing B02_AP1017_shift2_diagnostic_scene.FCStd untouched.
Generates:
1. reports/diagnostics/B02_AP1017_shift2_4_diagnostic_scene.FCStd
   and reports/diagnostics/B02_AP1017_shift2_4_diagnostic_scene.json
   (Stationary CONTACT pose at B02, s=+2.4 mm, B01=+90 deg, overlap=0.0 mm3 -> NO fake Common objects created).
2. reports/diagnostics/B02_AP1017_shift2_4_bending_u0875_scene.FCStd
   and reports/diagnostics/B02_AP1017_shift2_4_bending_u0875_scene.json
   (Unresolved BENDING interval start u=0.875, angle=+78.75 deg, s=+2.4 mm, sub-tolerance numerical overlap=1.2546e-5 mm3 < 1e-4 mm3).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import zipfile
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
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_punch, load_native_die, set_punch_ram_z, check_tool_coverage,
)
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.tooling_25d import ToolKind
import tools.run_25d_physical_gui_acceptance as acc


def _write_gui_document_xml(fcstd_path: Path, object_specs: list[tuple[str, int, int]]) -> None:
    """Write GuiDocument.xml into FCStd archive for color and transparency."""
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


def generate_single_scene(
    *,
    doc_name: str,
    out_fcstd: Path,
    out_json: Path,
    phase_name: str,
    path_u: float,
    b02_angle_deg: float,
    ram_z_mm: float,
    support_z_mm: float,
    shift_s_mm: float,
    segments: int,
    vol_tol_mm3: float = 1e-4,
) -> dict:
    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    part_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    doc_src = App.openDocument(str(fixture), True)
    panel = None
    diag_doc = None
    try:
        unfold = next((o for o in doc_src.Objects if o.Name.endswith("_Unfold")), None)
        if unfold is None:
            raise RuntimeError("UNFOLD_NOT_FOUND")

        session = build_part_session(doc_src, [unfold])
        panel = _PreviewPanel()
        panel.session = session
        panel._ensure_press_cycle_controller()

        prof_p = panel.tool_library.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH)
        prof_d = panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE)
        panel.select_punch(prof_p)
        panel.select_die(prof_d)
        panel.align_tooling()

        punch_path = Path(prof_p.source_file)
        die_path = Path(prof_d.source_file)
        punch_sha = hashlib.sha256(punch_path.read_bytes()).hexdigest()
        die_sha = hashlib.sha256(die_path.read_bytes()).hexdigest()

        session.fold_state.reset_all()
        target_b01 = float(session.fold_state.bends["B01"].angle_deg)
        session.fold_state.set_angle("B01", target_b01)
        session.fold_state.set_angle("B02", float(b02_angle_deg))
        session.select_bend_index(1)
        panel._sync_tooling_to_active_bend()

        placement = WorkpiecePlacement3D.compute_placement(
            session.fold_state,
            "B02",
            support_plane_zm=float(support_z_mm),
            align_bisector=True,
            align_bend_arc_center=True,
            desired_midpoint_xm=float(shift_s_mm),
        )

        wp_solid = build_multi_bend_sheet_solid(
            session.fold_state, session.panel_build, segments=int(segments),
        ).shape.copy()

        mat = App.Matrix(
            placement.rotation[0][0], placement.rotation[0][1], placement.rotation[0][2], placement.translation[0],
            placement.rotation[1][0], placement.rotation[1][1], placement.rotation[1][2], placement.translation[1],
            placement.rotation[2][0], placement.rotation[2][1], placement.rotation[2][2], placement.translation[2],
            0.0, 0.0, 0.0, 1.0,
        )
        wp_solid.transformShape(mat)

        punch_tool = load_native_punch(punch_path, ram_z_mm=float(ram_z_mm))
        die_tool = load_native_die(die_path)

        p_solid = set_punch_ram_z(punch_tool, float(ram_z_mm)).machine_shape.copy()
        d_solid = die_tool.machine_shape.copy()

        c_punch = wp_solid.common(p_solid)
        c_die = wp_solid.common(d_solid)
        p_ov_vol = float(c_punch.Volume) if hasattr(c_punch, "Volume") else 0.0
        d_ov_vol = float(c_die.Volume) if hasattr(c_die, "Volume") else 0.0
        p_dist = float(wp_solid.distToShape(p_solid)[0])
        d_dist = float(wp_solid.distToShape(d_solid)[0])

        diag_doc = App.newDocument(doc_name)
        object_specs = []

        wp_name = f"Workpiece_B01_90_B02_{int(round(b02_angle_deg))}deg_Shift2_4mm"
        o_wp = diag_doc.addObject("Part::Feature", wp_name)
        o_wp.Shape = wp_solid
        object_specs.append((wp_name, 3000673023, 40))

        p_name = f"Punch_AP1017_{phase_name}_Pose"
        o_p = diag_doc.addObject("Part::Feature", p_name)
        o_p.Shape = p_solid
        object_specs.append((p_name, 1296911871, 30))

        d_name = "Die_TDIE_AT3015"
        o_d = diag_doc.addObject("Part::Feature", d_name)
        o_d.Shape = d_solid
        object_specs.append((d_name, 1717987071, 30))

        # Strictly only include Common objects when actual intersection exceeds tolerance!
        # Never create fake/empty red Common objects when overlap is 0.0 or below volume_tolerance_mm3.
        included_common_objects = []
        if p_ov_vol > vol_tol_mm3:
            cp_name = "Common_Workpiece_Punch"
            o_cp = diag_doc.addObject("Part::Feature", cp_name)
            o_cp.Shape = c_punch
            object_specs.append((cp_name, 4278190335, 0))
            included_common_objects.append(cp_name)

        if d_ov_vol > vol_tol_mm3:
            cd_name = "Common_Workpiece_Die"
            o_cd = diag_doc.addObject("Part::Feature", cd_name)
            o_cd.Shape = c_die
            object_specs.append((cd_name, 4278255615, 0))
            included_common_objects.append(cd_name)

        diag_doc.recompute()
        diag_doc.saveAs(str(out_fcstd))
        _write_gui_document_xml(out_fcstd, object_specs)

        b02_bend = session.fold_state.bends["B02"]
        b_len = math.dist(b02_bend.axis_xyz[0], b02_bend.axis_xyz[1])
        shifted_span = (-0.5 * b_len + shift_s_mm, 0.5 * b_len + shift_s_mm)
        p_cov = check_tool_coverage(shifted_span, punch_tool.span_xm, punch_tool.sections)
        d_cov = check_tool_coverage(shifted_span, die_tool.span_xm, die_tool.sections)

        meta = {
            "scene_file": out_fcstd.name,
            "scene_path": str(out_fcstd),
            "scene_sha256": hashlib.sha256(out_fcstd.read_bytes()).hexdigest(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git": acc.get_git_info(),
            "freecad_version": ".".join(App.Version()[:3]),
            "part_file": fixture.name,
            "part_sha256": part_sha,
            "punch_file": punch_path.name,
            "punch_sha256": punch_sha,
            "die_file": die_path.name,
            "die_sha256": die_sha,
            "bend_order": list(session.bend_order),
            "active_bend_id": "B02",
            "prior_completed_angles_deg": {"B01": target_b01},
            "phase": phase_name,
            "path_u": float(path_u),
            "active_signed_angle_deg": float(b02_angle_deg),
            "longitudinal_shift_xm_mm": float(shift_s_mm),
            "ram_z_mm": float(ram_z_mm),
            "support_plane_zm_mm": float(support_z_mm),
            "arc_segments": int(segments),
            "volume_tolerance_mm3": float(vol_tol_mm3),
            "placement": {
                "translation_mm": [round(float(x), 6) for x in placement.translation],
                "rotation_matrix": [[round(float(v), 6) for v in row] for row in placement.rotation],
            },
            "coverage": {
                "bend_length_mm": round(float(b_len), 6),
                "unshifted_span_xm_mm": [round(-0.5 * b_len, 6), round(0.5 * b_len, 6)],
                "shifted_span_xm_mm": [round(shifted_span[0], 6), round(shifted_span[1], 6)],
                "punch_span_xm_mm": [float(punch_tool.span_xm[0]), float(punch_tool.span_xm[1])],
                "die_span_xm_mm": [float(die_tool.span_xm[0]), float(die_tool.span_xm[1])],
                "punch_is_covered": bool(p_cov.is_covered),
                "die_is_covered": bool(d_cov.is_covered),
                "punch_uncovered_left_mm": float(p_cov.uncovered_left_mm),
                "punch_uncovered_right_mm": float(p_cov.uncovered_right_mm),
                "die_uncovered_left_mm": float(d_cov.uncovered_left_mm),
                "die_uncovered_right_mm": float(d_cov.uncovered_right_mm),
            },
            "geometry_measurements": {
                "workpiece_is_valid": bool(wp_solid.isValid()),
                "workpiece_volume_mm3": round(float(wp_solid.Volume), 6),
                "punch_is_valid": bool(p_solid.isValid()),
                "punch_volume_mm3": round(float(p_solid.Volume), 6),
                "die_is_valid": bool(d_solid.isValid()),
                "die_volume_mm3": round(float(d_solid.Volume), 6),
                "punch_overlap_volume_mm3": p_ov_vol,
                "die_overlap_volume_mm3": d_ov_vol,
                "punch_distance_mm": p_dist,
                "die_distance_mm": d_dist,
                "exceeds_volume_tolerance": bool(p_ov_vol > vol_tol_mm3 or d_ov_vol > vol_tol_mm3),
                "included_common_objects": included_common_objects,
            },
            "scene_objects": [spec[0] for spec in object_specs],
        }
        out_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Generated scene: {out_fcstd} (JSON: {out_json})")
        print(f"  Phase={phase_name}, u={path_u}, angle={b02_angle_deg} deg, ram_z={ram_z_mm:.6f} mm, support_z={support_z_mm:.6f} mm")
        print(f"  Punch overlap={p_ov_vol:.9f} mm3 (dist={p_dist:.6f} mm), Die overlap={d_ov_vol:.9f} mm3 (dist={d_dist:.6f} mm)")
        print(f"  Included Common objects: {included_common_objects}")
        return meta
    finally:
        if diag_doc is not None:
            try:
                App.closeDocument(diag_doc.Name)
            except Exception:
                pass
        if panel is not None:
            try:
                panel.close()
                panel.deleteLater()
            except Exception:
                pass
        if doc_src is not None:
            try:
                App.closeDocument(doc_src.Name)
            except Exception:
                pass


def build_all_scenes():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    out_dir = ROOT / "reports" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stationary CONTACT pose at s = +2.4 mm (exact pose checked by check_current_25d_pose and CONTACT phase)
    generate_single_scene(
        doc_name="B02_AP1017_Shift2_4_Diagnostic",
        out_fcstd=out_dir / "B02_AP1017_shift2_4_diagnostic_scene.FCStd",
        out_json=out_dir / "B02_AP1017_shift2_4_diagnostic_scene.json",
        phase_name="CONTACT",
        path_u=0.0,
        b02_angle_deg=0.0,
        ram_z_mm=1.5,
        support_z_mm=2.5,
        shift_s_mm=2.4,
        segments=128,
    )

    # 2. Unresolved BENDING interval start pose (u = 0.875, angle = +78.75 deg) at s = +2.4 mm
    # Compute exact coupled kinematics for u = 0.875
    punch_tool = load_native_punch(ROOT / "toollibrary" / "punch" / "PUNCH_AP_1017_88°_H104,65_R0,8.FCStd", ram_z_mm=50.0)
    die_tool = load_native_die(ROOT / "toollibrary" / "die" / "T-DIE_AT_3015_88°_V12_H80.FCStd")
    eff_v = getattr(die_tool, "effective_v_opening_mm", die_tool.v_opening_mm) or die_tool.v_opening_mm
    coupled = CoupledAirBendingKinematics(
        die_opening_v_mm=eff_v,
        die_shoulder_radius_mm=die_tool.shoulder_radius_mm,
        die_angle_deg=die_tool.included_angle_deg,
        die_v_depth_mm=die_tool.v_depth_mm,
        punch_tip_radius_mm=punch_tool.tip_radius_mm,
        punch_included_angle_deg=punch_tool.included_angle_deg,
        sheet_thickness_mm=1.5,
        sheet_inner_radius_mm=1.0,
        die_top_z_mm=0.0,
    )
    u_val = 0.875
    theta_val = u_val * 90.0
    ram_z_u = coupled.ram_z_for_angle(theta_val)
    supp_z_u = -coupled.stroke_for_angle(theta_val) + 2.5

    generate_single_scene(
        doc_name="B02_AP1017_Shift2_4_Bending_U0875",
        out_fcstd=out_dir / "B02_AP1017_shift2_4_bending_u0875_scene.FCStd",
        out_json=out_dir / "B02_AP1017_shift2_4_bending_u0875_scene.json",
        phase_name="BENDING",
        path_u=u_val,
        b02_angle_deg=theta_val,
        ram_z_mm=ram_z_u,
        support_z_mm=supp_z_u,
        shift_s_mm=2.4,
        segments=128,
    )


if __name__ == "__main__":
    build_all_scenes()

