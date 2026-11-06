"""freecad_gui_d633_diagnostic.py - Interactive 3D Collision & Motion Diagnostic for FreeCAD Application.

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
6. Change the active static phase at any time by calling:
   show_phase("APPROACH")  # Options: "APPROACH", "CONTACT", "BENDING_45", "BOTTOM_90", "RETRACT"
7. Inspect full D6.3.4 multi-fixture adaptive trajectories and step through 3D samples:
   inspect_trajectory("L-(ok).FCStd", "B01")
   inspect_trajectory("Z-(ok).FCStd", "B02")
   inspect_trajectory("135-(ok).FCStd", "B02")
   show_sample(0)   # Step to specific sample index
   show_sample(25)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveTrajectoryValidator3D,
    AdaptiveSamplingConfig,
    TrajectoryEvidenceStatus,
    MotionState3D,
    BendTrajectoryResult,
)
from freecad_smbs.commands.pressbrake_2d import build_part_session

print("=" * 70)
print("SMBS BendSeq D6.3.3 / D6.3.4 FreeCAD Interactive 3D Motion Diagnostic")
print("=" * 70)

DIAG_DOC_NAME = "D633_Collision_Diagnostic"


def resolve_fixture_path(filename: str = "L-(ok).FCStd") -> Path:
    candidates = [
        REPO_ROOT / "BendSeq_Fixtures_Source" / filename,
        Path("/Users/badmc/Desktop/bend_Seq/BendSeq_Fixtures_Source") / filename,
        Path("/Users/badmc/Desktop/bend_Seq/SMBS/SMBendTests") / filename,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"Fixture {filename} not found in known fixture paths.")


# Tooling setup (200mm assembly)
punch_file = REPO_ROOT / "toollibrary" / "punch" / "puancon.FCStd"
die_file = REPO_ROOT / "toollibrary" / "die" / "matrica.FCStd"
punch_base = load_native_punch(punch_file, ram_z_mm=50.0, section_offsets_xm=[-50.0, 50.0])
die = load_native_die(die_file, section_offsets_xm=[-50.0, 50.0])

# 1. Close previous diagnostic doc if open (safe non-throwing existence check)
if DIAG_DOC_NAME in App.listDocuments():
    App.closeDocument(DIAG_DOC_NAME)

diag_doc = App.newDocument(DIAG_DOC_NAME)

# Create persistent visual features in diagnostic document
d_feat = diag_doc.addObject("Part::Feature", "Die_Assembly_200mm")
d_feat.Shape = die.machine_shape
if HAS_GUI and hasattr(d_feat, "ViewObject") and d_feat.ViewObject is not None:
    d_feat.ViewObject.ShapeColor = (0.2, 0.25, 0.35)

p_feat = diag_doc.addObject("Part::Feature", "Punch_Assembly_200mm")
p_feat.Shape = punch_base.machine_shape
if HAS_GUI and hasattr(p_feat, "ViewObject") and p_feat.ViewObject is not None:
    p_feat.ViewObject.ShapeColor = (0.5, 0.55, 0.6)

wp_feat = diag_doc.addObject("Part::Feature", "Workpiece_SheetSolid")
if HAS_GUI and hasattr(wp_feat, "ViewObject") and wp_feat.ViewObject is not None:
    wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3)

clash_feat = diag_doc.addObject("Part::Feature", "Clash_Interference_Highlight")
if HAS_GUI and hasattr(clash_feat, "ViewObject") and clash_feat.ViewObject is not None:
    clash_feat.ViewObject.ShapeColor = (1.0, 0.0, 0.6)  # Bright Magenta
    clash_feat.ViewObject.Transparency = 20


@dataclass
class ActiveVisualizationContext:
    """Explicit single active visualization context binding fixture, fold state, and samples."""
    fixture_name: str
    active_bend_id: str
    fold_state: Any
    panel_build: Any
    samples: List[MotionState3D] = field(default_factory=list)
    prior_completed_angles: Dict[str, float] = field(default_factory=dict)


# Single global active context
ACTIVE_CONTEXT: Optional[ActiveVisualizationContext] = None
ACTIVE_TRAJECTORY_CONTEXT: Optional[Dict[str, Any]] = None


def _update_view_mesh(wp_machine: Any, punch_machine: Any, is_safe: bool):
    """Update FreeCAD document objects with reconstructed shapes and colors."""
    p_feat.Shape = punch_machine
    wp_feat.Shape = wp_machine

    if HAS_GUI and hasattr(wp_feat, "ViewObject") and wp_feat.ViewObject is not None:
        if is_safe:
            wp_feat.ViewObject.ShapeColor = (0.2, 0.7, 0.3)  # Green
            clash_feat.Shape = Part.Shape()
        else:
            wp_feat.ViewObject.ShapeColor = (0.85, 0.2, 0.2)  # Red
            clash_solid = wp_machine.common(punch_machine)
            if die.machine_shape:
                die_clash = wp_machine.common(die.machine_shape)
                if die_clash.Volume > 0:
                    clash_solid = clash_solid.fuse(die_clash) if clash_solid.Volume > 0 else die_clash
            clash_feat.Shape = clash_solid

    diag_doc.recompute()
    if HAS_GUI and hasattr(Gui, "SendMsgToActiveView"):
        Gui.SendMsgToActiveView("ViewFit")


def show_phase(phase_name: str = "CONTACT"):
    """Original static 5-phase inspection (operates on active context or loads L-(ok).FCStd B01)."""
    global ACTIVE_CONTEXT
    if ACTIVE_CONTEXT is None or ACTIVE_CONTEXT.fixture_name != "L-(ok).FCStd":
        # Initialize default L-(ok) context
        _load_fixture_context("L-(ok).FCStd", "B01", run_validation=False)

    ctx = ACTIVE_CONTEXT
    st = ctx.fold_state
    bend = st.bends["B01"]
    bz = bend.bend_zone

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

    phases = {
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

    if phase_name not in phases:
        print(f"Unknown phase '{phase_name}'. Available: {list(phases.keys())}")
        return

    cfg = phases[phase_name]
    deg = cfg["angle"]
    ram_z = cfg["ram_z"]
    supp_z = cfg["supp_z"]

    st.set_angle("B01", deg)
    solid = build_multi_bend_sheet_solid(st, ctx.panel_build, segments=128)
    tf = WorkpiecePlacement3D.compute_placement(
        st, "B01", support_plane_zm=supp_z, align_bisector=True, align_bend_arc_center=True
    )
    mat_wp = App.Matrix(
        tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
        tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
        tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
        0.0, 0.0, 0.0, 1.0,
    )
    wp_machine = solid.shape.copy()
    wp_machine.transformShape(mat_wp)

    punch_step = set_punch_ram_z(punch_base, ram_z)
    eval_res = validate_workpiece_tooling_pose(wp_machine, punch_step.machine_shape, die.machine_shape)

    _update_view_mesh(wp_machine, punch_step.machine_shape, eval_res.is_safe)

    print(f"\n[D6.3.3 Visualizer] Phase: {phase_name} (Angle={deg:.1f}deg, Ram_Z={ram_z:.2f}mm)")
    print(f" - Status: {eval_res.status.value}, Safe: {eval_res.is_safe}")
    print(f" - Punch dist: {eval_res.punch_result.distance_mm:.4f} mm, Overlap: {eval_res.punch_result.overlap_volume_mm3:.6f} mm3")
    print(f" - Die dist:   {eval_res.die_result.distance_mm:.4f} mm, Overlap: {eval_res.die_result.overlap_volume_mm3:.6f} mm3")


def show_sample(sample_index: int):
    """Display a specific physical sample from the active inspected trajectory."""
    global ACTIVE_CONTEXT
    if ACTIVE_CONTEXT is None or not ACTIVE_CONTEXT.samples:
        print("No active trajectory loaded. Call inspect_trajectory('fixture.FCStd', 'bend_id') first.")
        return

    ctx = ACTIVE_CONTEXT
    if not (0 <= sample_index < len(ctx.samples)):
        print(f"Sample index {sample_index} out of range [0, {len(ctx.samples) - 1}].")
        return

    st_sample = ctx.samples[sample_index]
    st = ctx.fold_state

    # 1. Atomically apply complete angle state for this sample, reconstruct, and restore state
    try:
        # Reset all bends first
        st.reset_all()
        # Apply completed prior bends
        for b_id, b_ang in st_sample.completed_angles.items():
            st.set_angle(b_id, b_ang)
        # Apply active bend signed angle
        st.set_angle(st_sample.active_bend_id, st_sample.signed_angle_deg)

        # 2. Reconstruct workpiece solid with active context's panel_build
        solid = build_multi_bend_sheet_solid(st, ctx.panel_build, segments=128)
    finally:
        st.reset_all()

    # 3. Position workpiece using sample's recorded machine placement
    tf = st_sample.workpiece_placement
    mat_wp = App.Matrix(
        tf.rotation[0][0], tf.rotation[0][1], tf.rotation[0][2], tf.translation[0],
        tf.rotation[1][0], tf.rotation[1][1], tf.rotation[1][2], tf.translation[1],
        tf.rotation[2][0], tf.rotation[2][1], tf.rotation[2][2], tf.translation[2],
        0.0, 0.0, 0.0, 1.0,
    )
    wp_machine = solid.shape.copy()
    wp_machine.transformShape(mat_wp)

    # 4. Position punch
    punch_step = set_punch_ram_z(punch_base, st_sample.ram_z_mm)

    # 5. Update GUI shapes
    _update_view_mesh(wp_machine, punch_step.machine_shape, st_sample.is_safe)

    print(f"\n[D6.3.4 Sample #{sample_index}/{len(ctx.samples)-1}] Fixture={ctx.fixture_name} Bend={st_sample.active_bend_id} Phase={st_sample.phase} u={st_sample.path_u:.4f}")
    print(f" - Angle={st_sample.signed_angle_deg:.2f}deg, Ram_Z={st_sample.ram_z_mm:.2f}mm, Safe={st_sample.is_safe}")
    if st_sample.collision_report:
        p_res = st_sample.collision_report.punch_result
        d_res = st_sample.collision_report.die_result
        print(f" - Punch: dist={p_res.distance_mm:.4f} mm, overlap={p_res.overlap_volume_mm3:.6f} mm3, status={p_res.status.value}")
        print(f" - Die:   dist={d_res.distance_mm:.4f} mm, overlap={d_res.overlap_volume_mm3:.6f} mm3, status={d_res.status.value}")


def _load_fixture_context(
    fixture_name: str,
    bend_id: str,
    run_validation: bool = True,
) -> Optional[BendTrajectoryResult]:
    """Helper to safely build and activate an ActiveVisualizationContext."""
    global ACTIVE_CONTEXT
    f_path = resolve_fixture_path(fixture_name)
    s_doc = App.openDocument(str(f_path), True)
    try:
        u_obj = [o for o in s_doc.Objects if getattr(o, "Name", "").endswith("_Unfold")][0]
        sess = build_part_session(s_doc, [u_obj])
        st = sess.fold_state

        if bend_id not in st.bends:
            available = list(st.bends.keys())
            raise ValueError(f"Unknown bend '{bend_id}' in {fixture_name}. Available: {available}")

        # Compute prior completed angles: all ordered bends preceding bend_id are completed
        prior_angles: Dict[str, float] = {}
        for b in st.ordered_bends:
            if b.bend_id == bend_id:
                break
            prior_angles[b.bend_id] = float(b.angle_deg)

        res: Optional[BendTrajectoryResult] = None
        samples: List[MotionState3D] = []

        if run_validation:
            cfg = AdaptiveSamplingConfig(
                max_bend_angle_step_deg=10.0,
                max_ram_displacement_step_mm=5.0,
                min_clearance_margin_mm=0.5,
                clearance_change_threshold_mm=2.0,
                max_recursion_depth=6,
                max_evaluated_states_per_phase=48,
                max_phase_time_s=120.0,
                arc_segments=128,
            )
            validator = AdaptiveTrajectoryValidator3D(st, sess.panel_build, punch_base, die, cfg)
            res = validator.validate_bend_trajectory(bend_id, prior_angles)

            for pr in res.phase_results:
                samples.extend(pr.states)

        # Atomic replacement: activate new context only after successful completion
        ACTIVE_CONTEXT = ActiveVisualizationContext(
            fixture_name=fixture_name,
            active_bend_id=bend_id,
            fold_state=st,
            panel_build=sess.panel_build,
            samples=samples,
            prior_completed_angles=prior_angles,
        )
        global ACTIVE_TRAJECTORY_CONTEXT
        ACTIVE_TRAJECTORY_CONTEXT = {
            "fixture": fixture_name,
            "bend_id": bend_id,
            "state": st,
            "panel_build": sess.panel_build,
            "samples": samples,
            "prior_angles": prior_angles,
            "result": res,
        }
        return res
    finally:
        App.closeDocument(s_doc.Name)


def inspect_trajectory(fixture_name: str = "L-(ok).FCStd", bend_id: str = "B01"):
    """D6.3.4 Adaptive motion trajectory inspection and interactive sample navigation.

    Prints a concise summary (not giant dataclasses) and loads physical samples into show_sample().
    """
    print(f"\n[D6.3.4] Running adaptive trajectory validation on {fixture_name} ({bend_id})...")
    res = _load_fixture_context(fixture_name, bend_id, run_validation=True)
    if res is None or ACTIVE_CONTEXT is None:
        return

    print("----------------------------------------------------------------------")
    print(f"Fixture:                   {fixture_name} ({bend_id})")
    print(f"Evidence Status:           {res.evidence_status.value}")
    print(f"Continuous Certification:  {res.continuous_certification_status.value}")
    print(f"Setup Qualified:           {res.is_qualified}")
    print(f"Total Evaluated States:    {res.total_states_evaluated}")
    print(f"Subdivisions:              {res.total_subdivisions}")
    print(f"Solid Reconstructions:     {res.total_solid_reconstructions}")
    print(f"Collision Queries:         {res.total_collision_queries}")
    print(f"FoldState Restored:        {res.fold_state_restored}")
    print("Phase Summary:")
    for pr in res.phase_results:
        print(f" - {pr.phase_name:12s}: status={pr.evidence_status.value:28s} states={len(pr.states):2d} safe={pr.is_safe}")
    print("----------------------------------------------------------------------")
    print(f"Loaded {len(ACTIVE_CONTEXT.samples)} physical samples into viewer.")
    print("Navigate samples interactively with: show_sample(index)")

    if ACTIVE_CONTEXT.samples:
        show_sample(0)

    # Return concise summary dict rather than giant raw dataclass to prevent console flooding
    return {
        "fixture": fixture_name,
        "bend_id": bend_id,
        "evidence_status": res.evidence_status.value,
        "continuous_certification_status": res.continuous_certification_status.value,
        "is_qualified": res.is_qualified,
        "total_states": res.total_states_evaluated,
        "total_subdivisions": res.total_subdivisions,
        "total_reconstructions": res.total_solid_reconstructions,
        "total_collision_queries": res.total_collision_queries,
        "fold_state_restored": res.fold_state_restored,
    }


# Initial display: CONTACT phase on L-(ok).FCStd
show_phase("CONTACT")

print("\n" + "=" * 70)
print("Visualizer ready! Static 5-phase inspection:")
print(" - show_phase('APPROACH') / show_phase('CONTACT') / show_phase('BOTTOM_90')")
print("Inspect adaptive multi-fixture trajectories:")
print(" - inspect_trajectory('L-(ok).FCStd', 'B01')")
print(" - inspect_trajectory('Z-(ok).FCStd', 'B02')")
print(" - inspect_trajectory('135-(ok).FCStd', 'B02')")
print("Navigate physical samples:")
print(" - show_sample(0) ... show_sample(n)")
print("=" * 70)
