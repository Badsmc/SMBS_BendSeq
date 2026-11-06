"""Native FreeCAD acceptance for the integrated 2.5D + D6 collision check.

In FreeCAD Python Console, after installing feature/25d-physical-validation
and the original 17 demo models:
    from tools.run_25d_physical_gui_acceptance import run
    run()

This checker tests exactly one *stationary* real CAD pose. It never certifies
the complete path. The user verifies GUI after this test separately.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(*, diagnostic_only=False):
    import FreeCAD as App
    from PySide6 import QtWidgets
    from smbs_core.demo_paths import demo_part_path
    from smbs_core.kinematics2d.physical_validation_25d import (
        check_current_25d_pose, check_active_25d_bend_trajectory,
        PhysicalEvidence,
    )
    from freecad_smbs.commands.pressbrake_2d import (
        _PreviewPanel, build_part_session,
    )

    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError("FREECAD_GUI_REQUIRED")
    fixture = demo_part_path("box.100out-50in-(ok).FCStd")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    names_before = set(App.listDocuments())
    previously_active = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = None
    panel = None
    try:
        doc = App.openDocument(str(fixture), True)
        unfold = next(
            (o for o in doc.Objects if o.Name.endswith("_Unfold")),
            None,
        )
        if unfold is None:
            raise RuntimeError("ORIGINAL_UNFOLD_OBJECT_MISSING")
        session = build_part_session(doc, [unfold])
        panel = _PreviewPanel()
        panel.session = session
        panel._ensure_press_cycle_controller()
        panel.align_tooling()
        if not panel.tooling_setup.aligned:
            raise RuntimeError("TOOLING_NOT_ALIGNED:" + panel.status.text())
        initial = dict(session.fold_state.angles)

        # Diagnose a possible local-sheet vs machine-frame mismatch without
        # moving any tool, changing a FoldState or suppressing a real clash.
        from smbs_core.kinematics2d.machine_section_3d import (
            compute_machine_cutting_plane_section,
        )
        local_section = session.cross_section(segments=32)
        placement = panel.press_cycle.current_workpiece_placement_3d()
        machine_section = compute_machine_cutting_plane_section(
            session.fold_state, session.panel_vertices_xy, placement,
            active_bend_id=session.active_bend_id, segments=32,
        )
        def zone_summary(section):
            zone = section.get("zone_polygon") or ()
            neutral = section.get("neutral_curve") or ()
            return {
                "zone_bounds": (
                    (min(x for x, z in zone), min(z for x, z in zone),
                     max(x for x, z in zone), max(z for x, z in zone))
                    if zone else None
                ),
                "neutral_mid": (tuple(neutral[len(neutral)//2])
                                if neutral else None),
                "fixed_count": len(section.get("fixed_polygon") or ()),
            }
        print("PHYSICAL_FRAME_DIAGNOSTIC local:", zone_summary(local_section))
        print("PHYSICAL_FRAME_DIAGNOSTIC machine:", zone_summary(machine_section))
        # Compare the coordinate contracts used by the GUI and D6.3.4.
        # This must remain read-only: no 1.5mm compensating transform is
        # applied until verified against the actual die shoulder geometry.
        from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
        bend_id = session.active_bend_id
        bend = session.fold_state.bends[bend_id]
        axis_start, axis_end, _ = WorkpiecePlacement3D.get_world_bend_axis(
            session.fold_state, bend_id,
        )
        print("PHYSICAL_FRAME_DIAGNOSTIC axis_world:",
              axis_start, axis_end)
        print("PHYSICAL_FRAME_DIAGNOSTIC gui_placement:",
              "translation=", placement.translation,
              "rotation=", placement.rotation)
        d6_initial = WorkpiecePlacement3D.compute_placement(
            session.fold_state, bend_id,
            support_plane_zm=bend.bend_zone.outer_radius_mm,
            align_bisector=True, align_bend_arc_center=True,
        )
        print("PHYSICAL_FRAME_DIAGNOSTIC d634_initial_placement:",
              "translation=", d6_initial.translation,
              "rotation=", d6_initial.rotation)
        print("PHYSICAL_FRAME_DIAGNOSTIC frame_delta_xyz:",
              tuple(round(a-b, 6) for a, b in
                    zip(d6_initial.translation, placement.translation)))
        print("PHYSICAL_FRAME_DIAGNOSTIC tools:",
              "aligned=", panel.tooling_setup.aligned,
              "die_v=", panel.tooling_setup.die_v_center_machine(),
              "punch_tip=", panel.tooling_setup.punch_tip_machine(),
              "alignment_anchor=", panel.tooling_setup.alignment_anchor_sz,
              "ram=", panel.press_cycle.ram_z_mm)
        if not diagnostic_only:
            # Full GUI acceptance also exercises the real Qt control.
            panel.physical_check_btn.click()
        result = check_current_25d_pose(
            session, panel.press_cycle, panel.tooling_setup,
            native_3d=True,
        )
        if diagnostic_only:
            print("PHYSICAL_POSE_DIAGNOSTIC: {} | 2D={} | 3D={} | {}".format(
                result.status.value, result.section_status,
                result.native_status, result.reason,
            ))
        else:
            print("PHYSICAL_GUI_RESULT: " + panel.physical_check_result.text())
        print("PHYSICAL_NATIVE_STATUS: {} | {} | {}".format(
            result.status.value, result.native_status, result.reason,
        ))
        if dict(session.fold_state.angles) != initial:
            raise RuntimeError("FOLD_STATE_MUTATED_BY_COLLISION_CHECK")
        if result.is_fully_qualified:
            raise RuntimeError("INVALID_UNBOUNDED_QUALIFICATION")
        # A section collision is a valid finding but not proof of a native
        # 3D collision. Always run the independent D6.3.4 check so the
        # evidence mismatch can be diagnosed without masking either result.
        print("PHYSICAL_SECTION_EVIDENCE: {} | {} | {}".format(
            result.section_status, result.native_status, result.reason,
        ))
        if diagnostic_only:
            # Diagnostic-only mode deliberately does NOT print a PASS marker
            # and does NOT run the costly adaptive 120s trajectory. A real
            # native penetration remains a collision requiring correction.
            print("PHYSICAL_FRAME_DIAGNOSTIC_ONLY: no trajectory qualification")
            return {"pose": result, "trajectory": None}
        trajectory = check_active_25d_bend_trajectory(
            session, panel.tooling_setup,
        )
        print("D634_ADAPTIVE_RESULT: {} | {} | {}".format(
            trajectory.status.value, trajectory.native_status,
            trajectory.reason,
        ))
        if "3D_PUNCH_DIE_SAMPLED_BEND" not in trajectory.checked_scope:
            raise RuntimeError(
                "D634_NATIVE_EVIDENCE_MISSING: {} | {} | {}".format(
                    trajectory.status.value, trajectory.native_status, trajectory.reason,
                )
            )
        if trajectory.status is PhysicalEvidence.COLLISION:
            raise RuntimeError(
                "D634_COLLISION_DETECTED: {} | {}".format(
                    trajectory.native_status, trajectory.reason,
                )
            )
        if trajectory.status is not PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
            raise RuntimeError(
                "D634_ADAPTIVE_NOT_QUALIFIED: " + trajectory.reason
            )
        if result.section_status == "INTERFERENCE":
            raise RuntimeError(
                "SECTION_3D_EVIDENCE_CONFLICT: 2D section reports interference "
                "but D6 sampled 3D bend reports no penetration. Check "
                "machine-space die alignment and panel slicing. "
                "No physical acceptance granted."
            )
        if result.status is not PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
            raise RuntimeError(
                "POSE_NATIVE_EVIDENCE_UNRESOLVED: {} | {} | {}".format(
                    result.status.value, result.native_status, result.reason,
                )
            )
        if dict(session.fold_state.angles) != initial:
            raise RuntimeError("D634_FOLD_STATE_NOT_RESTORED")
        print("PHYSICAL_STAGE2_FREECAD_ACCEPTANCE_PASS: "
              "one CAD pose and sampled active bend, "
              "NOT_CONTINUOUSLY_CERTIFIED")
        return {"pose": result, "trajectory": trajectory}
    finally:
        if panel is not None:
            panel.timer.stop()
            panel.close()
            panel.deleteLater()
        if doc is not None and doc.Name not in names_before:
            App.closeDocument(doc.Name)
        if previously_active is not None and App.getDocument(previously_active) is not None:
            App.setActiveDocument(previously_active)
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("ORIGINAL_FCSTD_MODIFIED")


if __name__ == "__main__":
    run()
