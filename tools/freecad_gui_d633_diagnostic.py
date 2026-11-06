"""freecad_gui_d633_diagnostic.py - Interactive 3D Collision Diagnostic for FreeCAD Graphical Application.

Instructions for user:
1. Open FreeCAD (graphical interface).
2. Open View -> Panels -> Python console (if not already visible).
3. In Python Console, execute:
   exec(open("/Users/badmc/Desktop/SMBS_BendSeq/tools/freecad_gui_d633_diagnostic.py").read())
4. The script creates an isolated temporary diagnostic document ("D633_Collision_Diagnostic").
   Original fixture documents and any unsaved user documents are never modified or closed.
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

REPO_ROOT = Path(__file__).resolve().parents[1] if "__file__" in locals() else Path("/Users/badmc/Desktop/SMBS_BendSeq")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import FreeCAD as App
try:
    import FreeCADGui as Gui
    HAS_GUI = True
except ImportError:
    Gui = None
    HAS_GUI = False
import Part

from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch, set_punch_ram_z, check_tool_coverage
from smbs_core.kinematics2d.native_collision_3d import validate_workpiece_tooling_pose, qualify_operation_sampled_poses, PhaseQualificationReport, CollisionClass
from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_solid
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveTrajectoryValidator3D, AdaptiveSamplingConfig, TrajectoryEvidenceStatus
from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.demo_paths import find_demo_part

print("=" * 70)
print("SMBS BendSeq D6.3.3 / D6.3.4 FreeCAD Interactive 3D Motion Diagnostic")
print("=" * 70)

DIAG_DOC_NAME = "D633_Collision_Diagnostic"


def resolve_fixture_path(filename: str = "L-(ok).FCStd") -> Path:
    """Resolve demo sheet-metal fixtures relative to the installed repository."""
    return find_demo_part(filename)

fixture_path = resolve_fixture_path("L-(ok).FCStd")
punch_file = REPO_ROOT / "toollibrary" / "punch" / "puancon.FCStd"
die_file = REPO_ROOT / "toollibrary" / "die" / "matrica.FCStd"

# 1. Close previous diagnostic doc if open (safe non-throwing existence check)
if DIAG_DOC_NAME in App.listDocuments():
    App.closeDocument(DIAG_DOC_NAME)

diag_doc = App.newDocument(DIAG_DOC_NAME)

# 2. Read source fixture in background without altering it
src_doc = App.openDocument(str(fixture_path), True)
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

effective_v = getattr(die, "effective_v_opening_mm", die.v_opening_mm) or die.v_opening_mm

coupled = CoupledAirBendingKinematics(
    die_opening_v_mm=effective_v,
    die_shoulder_radius_mm=die.shoulder_radius_mm,
    die_angle_deg=die.included_angle_deg,
    die_v_depth_mm=die.v_depth_mm,
    punch_tip_radius_mm=punch_base.tip_radius_mm,
    punch_included_angle_deg=punch_base.included_angle_deg,
    sheet_thickness_mm=bz.thickness_mm,
    sheet_inner_radius_mm=bz.inner_radius_mm,
    die_top_z_mm=0.0,
)

# 4. Phase definitions (kinematically consistent & coaxial contact)
PHASES = {
    "APPROACH": {"angle": 0.0, "ram_z": 50.0, "supp_z": bz.outer_radius_mm},
    "CONTACT": {"angle": 0.0, "ram_z": coupled.contact_z_mm, "supp_z": bz.outer_radius_mm},
    "BENDING_45": {
        "angle": 45.0,
        "ram_z": coupled.ram_z_for_angle(45.0),
        "supp_z": -coupled.stroke_for_angle(45.0) + bz.outer_radius_mm,
    },
    "BOTTOM_90": {
        "angle": 90.0,
        "ram_z": coupled.ram_z_for_angle(90.0),
        "supp_z": -coupled.stroke_for_angle(90.0) + bz.outer_radius_mm,
    },
    "RETRACT": {
        "angle": 90.0,
        "ram_z": 40.0,
        "supp_z": -coupled.stroke_for_angle(90.0) + bz.outer_radius_mm,
    },
}

# 5. Create persistent diagnostic visual features in diagnostic document
d_feat = diag_doc.addObject("Part::Feature", "Die_Assembly_200mm")
d_feat.Shape = die.machine_shape
if HAS_GUI and hasattr(d_feat, "ViewObject") and d_feat.ViewObject is not None:
    d_feat.ViewObject.ShapeColor = (0.2, 0.25, 0.35)

p_feat = diag_doc.addObject("Part::Feature", "Punch_Assembly_200mm")
if HAS_GUI and hasattr(p_feat, "ViewObject") and p_feat.ViewObject is not None:
    p_feat.ViewObject.ShapeColor = (0.5, 0.55, 0.6)

wp_feat = diag_doc.addObject("Part::Feature", "Workpiece_SheetSolid")
if HAS_GUI and hasattr(wp_feat, "ViewObject") and wp_feat.ViewObject is not None:
    wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3)

clash_feat = diag_doc.addObject("Part::Feature", "Clash_Interference_Highlight")
if HAS_GUI and hasattr(clash_feat, "ViewObject") and clash_feat.ViewObject is not None:
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
    solid = build_multi_bend_sheet_solid(state, session.panel_build, segments=128)
    tf = WorkpiecePlacement3D.compute_placement(
        state,
        "B01",
        support_plane_zm=supp_z,
        align_bisector=True,
        align_bend_arc_center=True,
    )
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
    if HAS_GUI and hasattr(wp_feat, "ViewObject") and wp_feat.ViewObject is not None:
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
    if HAS_GUI and hasattr(Gui, "SendMsgToActiveView"):
        Gui.SendMsgToActiveView("ViewFit")

    print(f"\n[D6.3.3 Visualizer] Phase: {phase_name} (Angle={deg}deg, Ram_Z={ram_z:.2f}mm)")
    print(f" - Status: {eval_res.status.value}")
    print(f" - Safe: {eval_res.is_safe}")
    print(f" - Punch clearance: {eval_res.punch_result.distance_mm:.4f} mm, Overlap: {eval_res.punch_result.overlap_volume_mm3:.4f} mm3")
    print(f" - Die clearance:   {eval_res.die_result.distance_mm:.4f} mm, Overlap: {eval_res.die_result.overlap_volume_mm3:.4f} mm3")
    print(f" - Diagnostics: {eval_res.diagnostics}")


# Initial display: CONTACT phase
show_phase("CONTACT")


# The active sample viewer owns its fixture geometry. The static L fixture 'state'
# and 'session' above remain dedicated to show_phase() only.
ACTIVE_TRAJECTORY_CONTEXT = None


def _reconstruct_trajectory_sample(context, sample):
    """Build the chosen fixture's physical sample without contaminating FoldState."""
    selected_state = context["state"]
    if sample.active_bend_id not in selected_state.bends:
        raise ValueError(
            f"{context['fixture']}: unknown active bend {sample.active_bend_id}"
        )
    unknown = set(sample.completed_angles) - set(selected_state.bends)
    if unknown:
        raise ValueError(f"{context['fixture']}: unknown completed bends {sorted(unknown)}")

    # Explicitly initialize ALL bends: uncompleted bends stay flat, completed
    # bends retain their actual signed angles, and the active bend takes its sample.
    sample_angles = {bend_id: 0.0 for bend_id in selected_state.bends}
    sample_angles.update(sample.completed_angles)
    sample_angles[sample.active_bend_id] = sample.signed_angle_deg

    saved_angles = dict(selected_state.angles)
    saved_transforms = dict(selected_state.transforms)
    try:
        for bend_id in selected_state.bends:
            selected_state.set_angle(bend_id, sample_angles[bend_id])
        if selected_state.angles != sample_angles:
            raise RuntimeError("Sample angles were not applied correctly")
        solid = build_multi_bend_sheet_solid(
            selected_state, context["panel_build"], segments=context["arc_segments"]
        )
        tf = sample.workpiece_placement  # Authoritative recorded machine placement
        mat_wp = App.Matrix(
            tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
            tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
            tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
            0.0, 0.0, 0.0, 1.0,
        )
        wp_machine = solid.shape.copy()
        wp_machine.transformShape(mat_wp)
        return wp_machine
    finally:
        # GUI display is read-only with respect to the selected FoldState.
        selected_state.angles = saved_angles
        selected_state.transforms = saved_transforms
        if selected_state.angles != saved_angles or selected_state.transforms != saved_transforms:
            raise RuntimeError(f"{context['fixture']}: failed to restore viewer FoldState")


def show_sample(sample_index: int):
    """Display the real selected fixture's workpiece, tooling and saved collision result."""
    context = ACTIVE_TRAJECTORY_CONTEXT
    if context is None:
        print("No active trajectory. Call inspect_trajectory(fixture, bend_id) first.")
        return
    samples = context["samples"]
    if not (0 <= sample_index < len(samples)):
        print(f"Sample index {sample_index} out of range [0, {len(samples)-1}].")
        return

    sample = samples[sample_index]
    # Perform all potentially failing geometry operations BEFORE changing GUI shapes.
    workpiece = _reconstruct_trajectory_sample(context, sample)
    punch_step = set_punch_ram_z(context["punch"], sample.ram_z_mm)
    die_shape = context["die"].machine_shape
    collision = validate_workpiece_tooling_pose(
        workpiece, punch_step.machine_shape, die_shape
    )
    recorded = sample.collision_report
    if recorded is None or collision.status != recorded.status or collision.is_safe != recorded.is_safe:
        raise RuntimeError(
            f"{context['fixture']} {sample.active_bend_id} sample {sample_index}: "
            f"reconstructed collision differs from validated sample"
        )

    # Collision geometry is computed before any visual mutation.
    clash_shape = Part.Shape()
    if not collision.is_safe:
        clash_shape = workpiece.common(punch_step.machine_shape)
        die_clash = workpiece.common(die_shape)
        if die_clash.Volume > 0:
            clash_shape = clash_shape.fuse(die_clash) if clash_shape.Volume > 0 else die_clash

    p_feat.Shape = punch_step.machine_shape
    d_feat.Shape = die_shape
    wp_feat.Shape = workpiece
    clash_feat.Shape = clash_shape
    if HAS_GUI and getattr(wp_feat, "ViewObject", None) is not None:
        wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3) if collision.is_safe else (0.85, 0.2, 0.2)
    diag_doc.recompute()
    print(
        f"[D6.3.4 {context['fixture']} {sample.active_bend_id}] "
        f"Sample {sample_index}/{len(samples)-1} phase={sample.phase} "
        f"angle={sample.signed_angle_deg:.2f} deg ram={sample.ram_z_mm:.2f} mm "
        f"safe={collision.is_safe} punch_overlap={collision.punch_result.overlap_volume_mm3:.6f} mm3 "
        f"die_overlap={collision.die_result.overlap_volume_mm3:.6f} mm3"
    )


def inspect_trajectory(fixture_name: str = "L-(ok).FCStd", bend_id: str = "B01"):
    """Validate and atomically activate a fixture-specific native 3D sample viewer."""
    global ACTIVE_TRAJECTORY_CONTEXT
    print(f"[D6.3.4] Evaluating {fixture_name} {bend_id} in native FreeCAD...")
    source_path = resolve_fixture_path(fixture_name)
    source_doc = App.openDocument(str(source_path), True)
    try:
        unfold_objects = [o for o in source_doc.Objects if getattr(o, "Name", "").endswith("_Unfold")]
        if not unfold_objects:
            raise ValueError(f"{fixture_name}: no SheetMetal _Unfold object")
        selected_session = build_part_session(source_doc, [unfold_objects[0]])
        selected_state = selected_session.fold_state
        if bend_id not in selected_state.bends:
            raise ValueError(f"{fixture_name}: bend {bend_id} not found")

        cfg = AdaptiveSamplingConfig(
            max_bend_angle_step_deg=10.0,
            max_ram_displacement_step_mm=5.0,
            min_clearance_margin_mm=0.5,
            clearance_change_threshold_mm=2.0,
            max_recursion_depth=6,
            max_evaluated_states_per_phase=32,
            arc_segments=128,
        )
        # The requested B02 is evaluated with B01 already completed, not flat.
        prior_angles = {}
        for previous_bend in selected_state.ordered_bends:
            if previous_bend.bend_id == bend_id:
                break
            prior_angles[previous_bend.bend_id] = float(previous_bend.angle_deg)
        validator = AdaptiveTrajectoryValidator3D(
            selected_state, selected_session.panel_build, punch_base, die, cfg
        )
        result = validator.validate_bend_trajectory(bend_id, prior_angles)
        samples = [sample for phase in result.phase_results for sample in phase.states]
        if not samples:
            raise RuntimeError(f"{fixture_name} {bend_id}: no physical samples")

        candidate_context = {
            "fixture": fixture_name,
            "bend_id": bend_id,
            "state": selected_state,
            "panel_build": selected_session.panel_build,
            "samples": samples,
            "arc_segments": cfg.arc_segments,
            "punch": punch_base,
            "die": die,
        }
        # Verify the new context before replacing the old one.
        _reconstruct_trajectory_sample(candidate_context, samples[0])
    finally:
        App.closeDocument(source_doc.Name)

    ACTIVE_TRAJECTORY_CONTEXT = candidate_context
    print(
        f"Trajectory: {result.evidence_status.value}; "
        f"continuous: {result.continuous_certification_status.value}; "
        f"samples: {len(samples)}; qualified sampled poses: {result.is_qualified}"
    )
    show_sample(0)
    print(f"Navigate with show_sample(0..{len(samples)-1}).")
    # Do not return the huge BendTrajectoryResult to the interactive console.
    return None


print("\n" + "=" * 70)
print("Visualizer loaded! Call show_phase('PHASE_NAME') in console to inspect any phase:")
for p in PHASES:
    print(f" - show_phase('{p}')")
print("Or inspect full D6.3.4 motion trajectory:")
print(" - inspect_trajectory('L-(ok).FCStd', 'B01')")
print(" - inspect_trajectory('Z-(ok).FCStd', 'B02')")
print("Navigate samples with:")
print(" - show_sample(0) ... show_sample(n)")
print("=" * 70)
