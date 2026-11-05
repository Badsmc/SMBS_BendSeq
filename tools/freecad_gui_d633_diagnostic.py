"""freecad_gui_d633_diagnostic.py - Interactive 3D Collision Diagnostic for FreeCAD Graphical Application.

Instructions for user:
1. Open FreeCAD (graphical interface).
2. Open View -> Panels -> Python console (if not already visible).
3. In Python Console, execute:
   exec(open("/Users/badmc/Desktop/SMBS_BendSeq/tools/freecad_gui_d633_diagnostic.py").read())
4. The script creates an isolated temporary diagnostic document ("D633_Collision_Diagnostic").
   Original fixture documents are never saved or modified.
5. Live visual display includes:
   - Workpiece reconstructed 3D solid (Green if safe, Red if penetration)
   - Punch 200mm assembly (Steel Gray)
   - Die 200mm assembly (Dark Slate Gray)
   - Common intersection clash solid highlighted in Bright Magenta (if penetration occurs)
6. Change the active phase at any time by calling:
   show_phase("APPROACH")  # Options: "APPROACH", "CONTACT", "BENDING_45", "BOTTOM_90", "RETRACT"
"""
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/badmc/Desktop/SMBS_BendSeq")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import FreeCAD as App
import FreeCADGui as Gui
import Part

from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch, set_punch_ram_z, check_tool_coverage
from smbs_core.kinematics2d.native_collision_3d import validate_workpiece_tooling_pose, qualify_operation_sampled_poses, PhaseQualificationReport
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from freecad_smbs.commands.pressbrake_2d import build_part_session

print("=" * 70)
print("SMBS BendSeq D6.3.3 FreeCAD Interactive 3D Collision Diagnostic")
print("=" * 70)

DIAG_DOC_NAME = "D633_Collision_Diagnostic"
fixture_path = "/Users/badmc/Desktop/bend_Seq/BendSeq_Fixtures_Source/L-(ok).FCStd"
punch_file = REPO_ROOT / "toollibrary" / "punch" / "puancon.FCStd"
die_file = REPO_ROOT / "toollibrary" / "die" / "matrica.FCStd"

# 1. Close previous diagnostic doc if open (prevent duplicates)
if App.getDocument(DIAG_DOC_NAME):
    App.closeDocument(DIAG_DOC_NAME)

diag_doc = App.newDocument(DIAG_DOC_NAME)

# 2. Read source fixture without altering it
src_doc = App.openDocument(fixture_path, True)
try:
    unfold = [o for o in src_doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
    session = build_part_session(src_doc, [unfold])
    state = session.fold_state
    bend = state.bends["B01"]
    bz = bend.bend_zone
finally:
    App.closeDocument(src_doc.Name)

# 3. Load 200mm tooling assembly
punch_base = load_native_punch(punch_file, ram_z_mm=50.0, section_offsets_xm=[-50.0, 50.0])
die = load_native_die(die_file, section_offsets_xm=[-50.0, 50.0])

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

# 4. Phase definitions
PHASES = {
    "APPROACH": {"angle": 0.0, "ram_z": 50.0, "supp_z": bz.thickness_mm},
    "CONTACT": {"angle": 0.0, "ram_z": 1.5, "supp_z": bz.thickness_mm},
    "BENDING_45": {"angle": 45.0, "ram_z": coupled.ram_z_for_angle(45.0) + (bz.inner_radius_mm - punch_base.tip_radius_mm) + 0.1, "supp_z": coupled.support_plane_zm(45.0) + bz.thickness_mm},
    "BOTTOM_90": {"angle": 90.0, "ram_z": coupled.ram_z_for_angle(90.0) + (bz.inner_radius_mm - punch_base.tip_radius_mm) + 0.1, "supp_z": coupled.support_plane_zm(90.0) + bz.thickness_mm},
    "RETRACT": {"angle": 90.0, "ram_z": 40.0, "supp_z": coupled.support_plane_zm(90.0) + bz.thickness_mm},
}

# 5. Create persistent diagnostic visual features in diagnostic document
d_feat = diag_doc.addObject("Part::Feature", "Die_Assembly_200mm")
d_feat.Shape = die.machine_shape
d_feat.ViewObject.ShapeColor = (0.2, 0.25, 0.35)

p_feat = diag_doc.addObject("Part::Feature", "Punch_Assembly_200mm")
p_feat.ViewObject.ShapeColor = (0.5, 0.55, 0.6)

wp_feat = diag_doc.addObject("Part::Feature", "Workpiece_SheetSolid")
wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3)

clash_feat = diag_doc.addObject("Part::Feature", "Clash_Interference_Highlight")
clash_feat.ViewObject.ShapeColor = (1.0, 0.0, 0.6)  # Bright Magenta
clash_feat.ViewObject.Transparency = 20


def show_phase(phase_name: str = "CONTACT"):
    if phase_name not in PHASES:
        print(f"Unknown phase '{phase_name}'. Available: {list(PHASES.keys())}")
        return

    cfg = PHASES[phase_name]
    deg = cfg["angle"]
    ram_z = cfg["ram_z"]
    supp_z = cfg["supp_z"]

    # Reconstruct workpiece solid
    state.set_angle("B01", deg)
    solid = build_multi_bend_sheet_solid(state, session.panel_build, segments=32)
    tf = WorkpiecePlacement3D.compute_placement(state, "B01", support_plane_zm=supp_z, align_bisector=True)
    mat_wp = App.Matrix(
        tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
        tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
        tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
        0.0, 0.0, 0.0, 1.0,
    )
    wp_machine = solid.shape.copy()
    wp_machine.transformShape(mat_wp)

    # Position punch
    punch_step = set_punch_ram_z(punch_base, ram_z)

    # Evaluate collision
    eval_res = validate_workpiece_tooling_pose(wp_machine, punch_step.machine_shape, die.machine_shape)

    # Update visual shapes
    p_feat.Shape = punch_step.machine_shape
    wp_feat.Shape = wp_machine

    # Update workpiece color: Green if safe, Red if penetration
    if eval_res.is_safe:
        wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3)  # Green
        clash_feat.Shape = Part.Shape()  # empty
    else:
        wp_feat.ViewObject.ShapeColor = (0.85, 0.2, 0.2)  # Red
        # Highlight exact intersection solid
        clash_solid = wp_machine.common(punch_step.machine_shape)
        if die.machine_shape:
            die_clash = wp_machine.common(die.machine_shape)
            if die_clash.Volume > 0:
                clash_solid = clash_solid.fuse(die_clash) if clash_solid.Volume > 0 else die_clash
        clash_feat.Shape = clash_solid

    diag_doc.recompute()
    Gui.SendMsgToActiveView("ViewFit")

    print(f"\n[D6.3.3 Visualizer] Phase: {phase_name} (Angle={deg}deg, Ram_Z={ram_z:.2f}mm)")
    print(f" - Status: {eval_res.status.value}")
    print(f" - Safe: {eval_res.is_safe}")
    print(f" - Punch clearance: {eval_res.punch_result.distance_mm:.4f} mm, Overlap: {eval_res.punch_result.overlap_volume_mm3:.4f} mm3")
    print(f" - Die clearance:   {eval_res.die_result.distance_mm:.4f} mm, Overlap: {eval_res.die_result.overlap_volume_mm3:.4f} mm3")
    print(f" - Diagnostics: {eval_res.diagnostics}")


# Initial display: CONTACT phase
show_phase("CONTACT")
print("\n" + "=" * 70)
print("Visualizer loaded! Call show_phase('PHASE_NAME') in console to inspect any phase:")
for p in PHASES:
    print(f" - show_phase('{p}')")
print("=" * 70)
