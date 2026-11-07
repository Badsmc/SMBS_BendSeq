"""Generate diagnostic FreeCAD scene for Candidate Tooling & Workpiece inspection.
Outputs to reports/diagnostics/B02_AP1017_shift2_diagnostic_scene.FCStd
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

import FreeCAD as App
from PySide import QtWidgets
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die, set_punch_ram_z
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.tooling_25d import ToolKind


def build_scene():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    out_dir = ROOT / "reports" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "B02_AP1017_shift2_diagnostic_scene.FCStd"

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    doc_src = App.openDocument(str(fixture), True)
    unfold = next((o for o in doc_src.Objects if o.Name.endswith("_Unfold")), None)
    if unfold is None:
        raise RuntimeError("UNFOLD_NOT_FOUND")

    session = build_part_session(doc_src, [unfold])
    panel = _PreviewPanel()
    panel.session = session
    panel._ensure_press_cycle_controller()

    # Load candidate tools: PUNCH_AP_1017 + T-DIE_AT_3015
    prof_p = panel.tool_library.load_tool("PUNCH_AP_1017_88°_H104,65_R0,8", ToolKind.PUNCH)
    prof_d = panel.tool_library.load_tool("T-DIE_AT_3015_88°_V12_H80", ToolKind.DIE)
    panel.select_punch(prof_p)
    panel.select_die(prof_d)
    panel.align_tooling()

    # Step 1: Fold B01 to 90 degrees
    session.fold_state.set_angle("B01", 90.0)

    # Step 2: Select B02 as active bend
    session.select_bend_index(1)
    panel._sync_tooling_to_active_bend()

    # Placement with longitudinal shift s = +2.0 mm
    shift_s = 2.0
    bz = session.fold_state.bends["B02"].bend_zone
    placement = WorkpiecePlacement3D.compute_placement(
        session.fold_state,
        "B02",
        support_plane_zm=bz.outer_radius_mm,
        align_bisector=True,
        align_bend_arc_center=True,
        desired_midpoint_xm=shift_s,
    )

    wp_solid = build_multi_bend_sheet_solid(
        session.fold_state, session.panel_build, segments=64,
    ).shape.copy()

    mat = App.Matrix(
        placement.rotation[0][0], placement.rotation[0][1], placement.rotation[0][2], placement.translation[0],
        placement.rotation[1][0], placement.rotation[1][1], placement.rotation[1][2], placement.translation[1],
        placement.rotation[2][0], placement.rotation[2][1], placement.rotation[2][2], placement.translation[2],
        0.0, 0.0, 0.0, 1.0,
    )
    wp_solid.transformShape(mat)

    # Punch and die solids at contact pose (ram_z = 1.5 mm for contact with 1.5mm sheet)
    contact_ram_z = 1.5
    punch_tool = load_native_punch(prof_p.source_file, ram_z_mm=contact_ram_z)
    die_tool = load_native_die(prof_d.source_file)

    p_solid = set_punch_ram_z(punch_tool, contact_ram_z).machine_shape.copy()
    d_solid = die_tool.machine_shape.copy()

    # Check overlaps
    c_punch = wp_solid.common(p_solid)
    c_die = wp_solid.common(d_solid)

    diag_doc = App.newDocument("B02_AP1017_Shift2_Diagnostic")

    o_wp = diag_doc.addObject("Part::Feature", "Workpiece_Folded_B01_Shift2mm")
    o_wp.Shape = wp_solid

    o_p = diag_doc.addObject("Part::Feature", "Punch_AP1017_Contact_Pose")
    o_p.Shape = p_solid

    o_d = diag_doc.addObject("Part::Feature", "Die_TDIE_AT3015")
    o_d.Shape = d_solid

    o_cp = diag_doc.addObject("Part::Feature", "Common_Workpiece_Punch")
    o_cp.Shape = c_punch

    o_cd = diag_doc.addObject("Part::Feature", "Common_Workpiece_Die")
    o_cd.Shape = c_die

    diag_doc.recompute()
    diag_doc.saveAs(str(out_file))

    gui_xml = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1" HasExpansion="1">
    <ViewProviderData Count="5">
        <ViewProvider name="Workpiece_Folded_B01_Shift2mm">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="3000673023"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="40"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Punch_AP1017_Contact_Pose">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="1296911871"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="30"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Die_TDIE_AT3015">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="1717987071"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="30"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Common_Workpiece_Punch">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="4278190335"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="0"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Common_Workpiece_Die">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1"><Integer value="0"/></Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1"><PropertyColor value="4278255615"/></Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1"><Integer value="0"/></Property>
                <Property name="Visibility" type="App::PropertyBool" status="1"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
    </ViewProviderData>
    <CameraSettings>
        <ViewCamera value="OrthographicCamera"/>
    </CameraSettings>
</Document>
"""
    with zipfile.ZipFile(out_file, "a") as zf:
        zf.writestr("GuiDocument.xml", gui_xml)

    print(f"Generated diagnostic scene at: {out_file}")
    print(f"  Workpiece volume: {wp_solid.Volume:.5f} mm³")
    print(f"  Punch volume: {p_solid.Volume:.5f} mm³")
    print(f"  Die volume: {d_solid.Volume:.5f} mm³")
    print(f"  Common overlap with punch: {c_punch.Volume:.5f} mm³")
    print(f"  Common overlap with die: {c_die.Volume:.5f} mm³")

    App.closeDocument(diag_doc.Name)
    App.closeDocument(doc_src.Name)
    panel.close()
    panel.deleteLater()


if __name__ == "__main__":
    build_scene()
