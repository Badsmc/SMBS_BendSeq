#!/usr/bin/env python3
"""Stage D4-P1 Verification: Punch-Sheet-Die Coupled Kinematics on box.100out-50in-(ok) 2.FCStd."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

# Setup paths
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
fc_lib_path = "/Applications/FreeCAD.app/Contents/Resources/lib"
if os.path.exists(fc_lib_path) and fc_lib_path not in sys.path:
    sys.path.insert(0, fc_lib_path)

import FreeCAD
from PySide6 import QtWidgets, QtCore

from freecad_smbs.commands.pressbrake_2d import _PreviewPanel, build_part_session
from smbs_core.kinematics2d.press_cycle import CycleExecutionMode, CyclePhase
from smbs_core.kinematics2d.tooling_25d import ToolKind, ValidationStatus

MODEL_PATH = "/Users/badmc/Desktop/\u041d\u043e\u0432\u0430\u044f \u043f\u0430\u043f\u043a\u0430/box.100out-50in-(ok) 2.FCStd"


def run_verification():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    doc = FreeCAD.openDocument(MODEL_PATH)
    try:
        obj = next(o for o in doc.Objects if o.Name == "Body_Unfold")
        session = build_part_session(doc, [obj])
        print(f"Loaded session with {len(session.bend_order)} bends: {session.bend_order}")

        panel = _PreviewPanel()
        panel.session = session
        panel.execution_mode = CycleExecutionMode.FULL_CYCLE

        # Ensure verified tooling is loaded and active
        punch = panel.tooling_setup.punch
        die = panel.tooling_setup.die
        print(f"Tooling setup: punch={punch.tool_id if punch else None}, die={die.tool_id if die else None}")
        assert punch is not None, "Punch must be auto-selected"
        assert die is not None, "Die must be auto-selected"

        # Check controller
        pc = panel._ensure_press_cycle_controller()
        assert pc.coupled_kinematics is not None, "Coupled kinematics must be initialized"
        ck = pc.coupled_kinematics
        print(f"Coupled kinematics initialized: V={ck.die_opening_v_mm}mm, Rd={ck.die_shoulder_radius_mm}mm, Rp={ck.punch_tip_radius_mm}mm")

        # Start motion trace
        panel.trace_checkbox.setChecked(True)
        panel._start_motion_trace(manual=True)
        trace_path = panel.motion_trace.path
        print(f"Started trace at: {trace_path}")

        # Start simulation
        panel.play_all()

        max_ticks = 4000
        ticks = 0
        bends_seen = set()
        phases_seen_b01 = []
        b01_penetration_seen = False
        b01_stroke_max = 0.0

        while ticks < max_ticks:
            if panel.session.controller.is_bend_completed("B08") and pc.phase in (CyclePhase.STEP_COMPLETE, CyclePhase.COMPLETED, CyclePhase.IDLE):
                print(f"All 8 bends completed successfully at tick {ticks}!")
                break

            cur_bend = panel.session.active_bend_id
            bends_seen.add(cur_bend)

            if cur_bend == "B01":
                phases_seen_b01.append(pc.phase.value)
                if pc.phase == CyclePhase.BENDING:
                    b01_stroke_max = max(b01_stroke_max, pc.ram_stroke_mm)
                    if pc.ram_stroke_mm > 10.0:
                        b01_penetration_seen = True

            panel.advance()
            ticks += 1

        summary_path = panel._finish_motion_trace("D4_P1_VERIFICATION_COMPLETE")
        print(f"Simulation completed in {ticks} ticks. Trace summary: {summary_path}")

        # Verification checks
        assert "B01" in bends_seen and "B08" in bends_seen, f"Expected all bends seen, got {bends_seen}"
        assert len(bends_seen) == 8, f"Expected 8 bends, got {len(bends_seen)}"
        assert b01_penetration_seen, f"B01 did not penetrate V-die! Max stroke was {b01_stroke_max}"
        print(f"B01 max stroke during BENDING: {b01_stroke_max:.2f} mm")

        # Parse trace JSONL to verify coupled fields
        with open(trace_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        frames = [l for l in lines if l.get("kind") == "FRAME"]
        print(f"Recorded {len(frames)} frames in trace")

        # Verify telemetry fields exist in frame records
        b01_bending_frames = [f for f in frames if f.get("active_bend_id") == "B01" and f.get("cycle_phase") == "BENDING"]
        assert len(b01_bending_frames) > 0, "No B01 BENDING frames recorded"

        sample = b01_bending_frames[len(b01_bending_frames) // 2]
        print("Sample B01 BENDING frame telemetry:")
        for k in ("punch_stroke_mm", "bend_angle_deg", "contact_points", "bend_zone_position",
                  "punch_clearance_mm", "validation_status", "collision_validated", "support_validated"):
            assert k in sample, f"Missing telemetry field '{k}' in trace record"
            print(f"  {k}: {sample[k]}")

        assert sample["collision_validated"] is False
        assert sample["support_validated"] is False
        assert sample["contact_points"]["punch"] is not None
        assert sample["contact_points"]["die_left"] is not None
        assert sample["contact_points"]["die_right"] is not None

        # Verify punch approaches without premature angle change
        approach_frames = [f for f in frames if f.get("active_bend_id") == "B01" and f.get("cycle_phase") == "APPROACH"]
        for af in approach_frames:
            assert af["angles_deg"]["B01"] == 0.0, f"Premature angle change in APPROACH: {af['angles_deg']['B01']}"

        # Verify retract doesn't unbend the sheet
        retract_frames = [f for f in frames if f.get("active_bend_id") == "B01" and f.get("cycle_phase") == "RETRACT"]
        assert len(retract_frames) > 0, "No B01 RETRACT frames recorded"
        for rf in retract_frames:
            assert abs(rf["angles_deg"]["B01"] - 90.0) < 1e-3, f"Sheet unbent during RETRACT: {rf['angles_deg']['B01']}"

        print("\n>>> ALL D4-P1 VERIFICATION CHECKS PASSED SUCCESSFULLY! <<<")
    finally:
        FreeCAD.closeDocument(doc.Name)


if __name__ == "__main__":
    run_verification()
