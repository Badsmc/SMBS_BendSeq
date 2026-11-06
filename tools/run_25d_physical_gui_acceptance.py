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


def run():
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
        panel.physical_check_btn.click()
        result = check_current_25d_pose(
            session, panel.press_cycle, panel.tooling_setup,
            native_3d=True,
        )
        print("PHYSICAL_GUI_RESULT: " + panel.physical_check_result.text())
        print("PHYSICAL_NATIVE_STATUS: {} | {} | {}".format(
            result.status.value, result.native_status, result.reason,
        ))
        if dict(session.fold_state.angles) != initial:
            raise RuntimeError("FOLD_STATE_MUTATED_BY_COLLISION_CHECK")
        if result.is_fully_qualified:
            raise RuntimeError("INVALID_UNBOUNDED_QUALIFICATION")
        # A real collision is a valid *diagnostic result*, not a successful
        # production cycle; both collision and sampled clear have evidence.
        if result.status in (
            PhysicalEvidence.UNVERIFIED,
            PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED,
        ):
            raise RuntimeError(
                "NATIVE_3D_NOT_YET_QUALIFIED_FOR_FIXTURE: " + result.reason
            )
        if "3D_PUNCH_DIE_CURRENT_POSE" not in result.checked_scope:
            raise RuntimeError("NO_NATIVE_3D_EVIDENCE")
        print("PHYSICAL_GUI_SAMPLE_CHECK_DONE: {} (single pose only)".format(
            result.status.value
        ))
        trajectory = check_active_25d_bend_trajectory(
            session, panel.tooling_setup,
        )
        print("D634_ADAPTIVE_RESULT: {} | {} | {}".format(
            trajectory.status.value, trajectory.native_status,
            trajectory.reason,
        ))
        if trajectory.status is not PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS:
            raise RuntimeError(
                "D634_ADAPTIVE_NOT_QUALIFIED: " + trajectory.reason
            )
        if "3D_PUNCH_DIE_SAMPLED_BEND" not in trajectory.checked_scope:
            raise RuntimeError("D634_NO_SAMPLED_BEND_EVIDENCE")
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
