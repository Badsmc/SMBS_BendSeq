"""Parametric generator for FreeCAD diagnostic scenes across bends and shifts.

Supports:
- Distinguishing multiple connected collision solids with separate objects and colors.
- Custom bend sequences (e.g. B01->B02->B04->B03).
- Arbitrary per-bend prior angles and shifts.
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
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_punch, load_native_die, set_punch_ram_z, check_tool_coverage,
)
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.tooling_25d import ToolKind
import tools.run_25d_physical_gui_acceptance as acc


def write_gui_document_xml(fcstd_path: Path, object_specs: list[tuple[str, int, int]]) -> None:
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


def generate_scene(
    *,
    bend_id: str,
    prior_angles: dict[str, float],
    active_angle_deg: float,
    shift_s_mm: float,
    ram_z_mm: float,
    support_z_mm: float,
    phase_name: str,
    path_u: float,
    out_fcstd: Path,
    out_json: Path,
    custom_bend_order: list[str] | tuple[str, ...] | None = None,
    punch_id: str = "PUNCH_AP_1017_88°_H104,65_R0,8",
    die_id: str = "T-DIE_AT_3015_88°_V12_H80",
    segments: int = 128,
    vol_tol_mm3: float = 1e-4,
) -> dict:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
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
        if custom_bend_order:
            session.set_bend_order(list(custom_bend_order))

        panel = _PreviewPanel()
        panel.session = session
        panel._ensure_press_cycle_controller()

        prof_p = panel.tool_library.load_tool(punch_id, ToolKind.PUNCH)
        prof_d = panel.tool_library.load_tool(die_id, ToolKind.DIE)
        panel.select_punch(prof_p)
        panel.select_die(prof_d)
        panel.align_tooling()

        punch_path = Path(prof_p.source_file)
        die_path = Path(prof_d.source_file)
        punch_sha = hashlib.sha256(punch_path.read_bytes()).hexdigest()
        die_sha = hashlib.sha256(die_path.read_bytes()).hexdigest()

        session.fold_state.reset_all()
        for b_name, ang in prior_angles.items():
            session.fold_state.set_angle(b_name, float(ang))
        session.fold_state.set_angle(bend_id, float(active_angle_deg))

        bend_idx = list(session.bend_order).index(bend_id)
        session.select_bend_index(bend_idx)
        panel._sync_tooling_to_active_bend()

        placement = WorkpiecePlacement3D.compute_placement(
            session.fold_state,
            bend_id,
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

        doc_name = f"Diag_{bend_id}_s{str(shift_s_mm).replace('.', '_').replace('-', 'm')}"
        diag_doc = App.newDocument(doc_name)
        object_specs = []

        wp_name = f"Workpiece_{bend_id}_{int(round(active_angle_deg))}deg"
        o_wp = diag_doc.addObject("Part::Feature", wp_name)
        o_wp.Shape = wp_solid
        object_specs.append((wp_name, 3000673023, 40))

        p_name = f"Punch_{punch_id[:12]}_{phase_name}"
        o_p = diag_doc.addObject("Part::Feature", p_name)
        o_p.Shape = p_solid
        object_specs.append((p_name, 1296911871, 30))

        d_name = f"Die_{die_id[:12]}"
        o_d = diag_doc.addObject("Part::Feature", d_name)
        o_d.Shape = d_solid
        object_specs.append((d_name, 1717987071, 30))

        collision_solids_meta = []
        included_common_objects = []

        # Palette for multiple collision components: Red, Magenta, Yellow, Orange
        comp_colors = [4278190335, 4278255615, 16711935, 4286578943]

        if p_ov_vol > vol_tol_mm3:
            solids = list(c_punch.Solids) if hasattr(c_punch, "Solids") and c_punch.Solids else [c_punch]
            for s_idx, sol in enumerate(solids):
                vol_sol = float(sol.Volume) if hasattr(sol, "Volume") else 0.0
                if vol_sol <= vol_tol_mm3:
                    continue
                cm = sol.CenterOfMass
                bb = sol.BoundBox
                c_name = f"Common_Punch_Component_{s_idx+1}" if len(solids) > 1 else "Common_Workpiece_Punch"
                o_c = diag_doc.addObject("Part::Feature", c_name)
                o_c.Shape = sol
                color_val = comp_colors[s_idx % len(comp_colors)]
                object_specs.append((c_name, color_val, 0))
                included_common_objects.append(c_name)
                collision_solids_meta.append({
                    "object_name": c_name,
                    "target": "PUNCH",
                    "volume_mm3": round(vol_sol, 6),
                    "center_of_mass_mm": [round(cm.x, 3), round(cm.y, 3), round(cm.z, 3)],
                    "bounding_box_mm": {
                        "min": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
                        "max": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)],
                    },
                })

        if d_ov_vol > vol_tol_mm3:
            cd_name = "Common_Workpiece_Die"
            o_cd = diag_doc.addObject("Part::Feature", cd_name)
            o_cd.Shape = c_die
            object_specs.append((cd_name, 4278255615, 0))
            included_common_objects.append(cd_name)

        diag_doc.recompute()
        out_fcstd.parent.mkdir(parents=True, exist_ok=True)
        diag_doc.saveAs(str(out_fcstd))
        write_gui_document_xml(out_fcstd, object_specs)

        bend_obj = session.fold_state.bends[bend_id]
        b_len = math.dist(bend_obj.axis_xyz[0], bend_obj.axis_xyz[1])
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
            "active_bend_id": bend_id,
            "prior_completed_angles_deg": prior_angles,
            "phase": phase_name,
            "path_u": float(path_u),
            "active_signed_angle_deg": float(active_angle_deg),
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
                "collision_components": collision_solids_meta,
            },
            "scene_objects": [spec[0] for spec in object_specs],
        }
        out_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Generated scene: {out_fcstd} (JSON: {out_json})")
        print(f"  Bend={bend_id}, Phase={phase_name}, s={shift_s_mm:+.2f}mm, ram_z={ram_z_mm:.4f}mm")
        print(f"  Punch overlap={p_ov_vol:.6f} mm3, Die overlap={d_ov_vol:.6f} mm3")
        print(f"  Components: {len(collision_solids_meta)}")
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


def main():
    out_dir = ROOT / "reports" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Canonical B04 collision scene with 2 distinct colored components (s = 0.0 mm)
    generate_scene(
        bend_id="B04",
        prior_angles={"B01": 90.0, "B02": 90.0, "B03": 90.0},
        active_angle_deg=0.0,
        shift_s_mm=0.0,
        ram_z_mm=6.5,
        support_z_mm=2.5,
        phase_name="CONTACT",
        path_u=0.0,
        out_fcstd=out_dir / "B04_AP1017_shift0_contact_collision_scene.FCStd",
        out_json=out_dir / "B04_AP1017_shift0_contact_collision_scene.json",
    )

    # 2. Alternative sequence: B04 step 3 clear candidate (order B01->B02->B04, s = -2.5 mm, CLEAR)
    generate_scene(
        bend_id="B04",
        prior_angles={"B01": 90.0, "B02": 90.0},
        active_angle_deg=0.0,
        shift_s_mm=-2.5,
        ram_z_mm=6.5,
        support_z_mm=2.5,
        phase_name="CONTACT",
        path_u=0.0,
        custom_bend_order=["B01", "B02", "B04", "B03", "B05", "B06", "B07", "B08"],
        out_fcstd=out_dir / "B04_reordered_step3_shift_m2_5_clear_scene.FCStd",
        out_json=out_dir / "B04_reordered_step3_shift_m2_5_clear_scene.json",
    )

    # 3. Alternative sequence: B03 step 4 collision obstacle (order B01->B02->B04->B03, s = 0.0 mm, COLLISION)
    generate_scene(
        bend_id="B03",
        prior_angles={"B01": 90.0, "B02": 90.0, "B04": 90.0},
        active_angle_deg=0.0,
        shift_s_mm=0.0,
        ram_z_mm=6.5,
        support_z_mm=2.5,
        phase_name="CONTACT",
        path_u=0.0,
        custom_bend_order=["B01", "B02", "B04", "B03", "B05", "B06", "B07", "B08"],
        out_fcstd=out_dir / "B03_reordered_step4_shift0_collision_scene.FCStd",
        out_json=out_dir / "B03_reordered_step4_shift0_collision_scene.json",
    )


if __name__ == "__main__":
    main()
