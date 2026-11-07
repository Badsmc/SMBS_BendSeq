"""Generate diagnostic FreeCAD scene for B02 collision inspection.
Saves to reports/diagnostics/B02_collision_scene.FCStd.
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
from PySide6 import QtWidgets
from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.native_tool_loader import load_native_punch, load_native_die, set_punch_ram_z


def build_scene():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    out_dir = ROOT / "reports" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "B02_collision_scene.FCStd"

    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    doc_src = App.openDocument(str(fixture), True)
    unfold = next((o for o in doc_src.Objects if o.Name.endswith("_Unfold")), None)
    if unfold is None:
        raise RuntimeError("UNFOLD_NOT_FOUND")

    session = build_part_session(doc_src, [unfold])
    panel = _PreviewPanel()
    panel.session = session
    panel._ensure_press_cycle_controller()
    panel.align_tooling()

    # Step 1: Fold B01 to 90 degrees
    session.fold_state.set_angle("B01", 90.0)

    # Step 2: Select B02 as active bend
    session.select_bend_index(1)

    # Step 3: Exact machine placement from press_cycle
    placement = panel.press_cycle.current_workpiece_placement_3d()

    # Build authoritative workpiece solid with B01 folded and B02 active
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

    # Load punch and die solids at contact pose (ram_z = 6.5 mm)
    punch_path = Path(panel.tooling_setup.punch.source_file)
    die_path = Path(panel.tooling_setup.die.source_file)
    punch_tool = load_native_punch(punch_path, ram_z_mm=float(panel.press_cycle.ram_z_mm))
    die_tool = load_native_die(die_path)

    p_solid = set_punch_ram_z(punch_tool, float(panel.press_cycle.ram_z_mm)).machine_shape.copy()
    d_solid = die_tool.machine_shape.copy()

    # Compute common intersection shape
    c_punch = wp_solid.common(p_solid)

    # Create new diagnostic document
    diag_doc = App.newDocument("B02_Collision_Scene")

    o_wp = diag_doc.addObject("Part::Feature", "Workpiece_Folded_B01_Active_B02")
    o_wp.Shape = wp_solid

    o_p = diag_doc.addObject("Part::Feature", "Punch_Puancon_Ram6_5")
    o_p.Shape = p_solid

    o_d = diag_doc.addObject("Part::Feature", "Die_Matrica")
    o_d.Shape = d_solid

    o_c = diag_doc.addObject("Part::Feature", "Common_Workpiece_Punch")
    o_c.Shape = c_punch

    diag_doc.recompute()
    diag_doc.saveAs(str(out_file))

    # Add GuiDocument.xml for proper viewer colors and transparency
    gui_xml = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1" HasExpansion="1">
    <ViewProviderData Count="4">
        <ViewProvider name="Workpiece_Folded_B01_Active_B02">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1">
                    <PropertyColor value="3000673023"/>
                </Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1">
                    <Integer value="50"/>
                </Property>
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Punch_Puancon_Ram6_5">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1">
                    <PropertyColor value="1296911871"/>
                </Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1">
                    <Integer value="30"/>
                </Property>
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Die_Matrica">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1">
                    <PropertyColor value="1717987071"/>
                </Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1">
                    <Integer value="30"/>
                </Property>
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
            </Properties>
        </ViewProvider>
        <ViewProvider name="Common_Workpiece_Punch">
            <Properties Count="4" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="ShapeColor" type="App::PropertyColor" status="1">
                    <PropertyColor value="4278190335"/>
                </Property>
                <Property name="Transparency" type="App::PropertyPercent" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
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
    print(f"  Workpiece volume: {wp_solid.Volume:.5f} mm³ (valid={wp_solid.isValid()})")
    print(f"  Punch volume: {p_solid.Volume:.5f} mm³ (valid={p_solid.isValid()})")
    print(f"  Die volume: {d_solid.Volume:.5f} mm³ (valid={d_solid.isValid()})")
    print(f"  Common overlap volume: {c_punch.Volume:.5f} mm³ (valid={c_punch.isValid()})")

    # Clean up
    App.closeDocument(diag_doc.Name)
    App.closeDocument(doc_src.Name)
    panel.close()
    panel.deleteLater()


if __name__ == "__main__":
    build_scene()
