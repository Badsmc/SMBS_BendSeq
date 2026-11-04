"""Headless tests for per-frame PressBrakeSim motion tracing (no FreeCAD)."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from smbs_core.kinematics2d.motion_trace import (
    MotionTraceRecorder, make_frame_record,
)
from smbs_core.kinematics2d.workpiece_pose_2d import WorkpiecePose2D


class FakeTooling:
    aligned = True
    punch_machine_transform = None
    die_machine_transform = None

    def punch_tip_machine(self):
        return (0.0, 12.0)

    def die_v_center_machine(self):
        return (0.0, -2.0)


def section(shift=0.0):
    return {
        "fixed_polygon": ((-50.0+shift, 0.0),(-1.0+shift,0.0),
                          (-1.0+shift,-2.0),(-50.0+shift,-2.0)),
        "moving_polygon": ((1.0+shift,0.0),(50.0+shift,0.0),
                           (50.0+shift,-2.0),(1.0+shift,-2.0)),
        "zone_polygon": ((-1.0+shift,0.0),(1.0+shift,0.0),
                         (1.0+shift,-2.0),(-1.0+shift,-2.0)),
        "inner_surface": ((-50.0+shift,-2.0),(50.0+shift,-2.0)),
        "outer_surface": ((-50.0+shift,0.0),(50.0+shift,0.0)),
        "neutral_curve": ((-1.0+shift,-1.0),(1.0+shift,-1.0)),
        "other_panel_polygons": (("P03",((50+shift,0),(60+shift,0))),),
    }


class FakeSession:
    active_bend_id = "B01"
    active_bend_index = 0
    bend_order = ("B01", "B02")
    canonical_order = ("B01", "B02")
    playback_status = SimpleNamespace(value="PLAYING")
    fold_state = SimpleNamespace(angles={"B01":90.0,"B02":0.0})


class FakeCycle:
    phase = SimpleNamespace(value="BENDING")
    ram_z_mm = 2.0
    ram_stroke_mm = 10.0
    transfer_progress = 0.0
    transfer_duration_s = 1.8


class MotionTraceTests(unittest.TestCase):
    def test_serializes_all_real_positions(self):
        row = make_frame_record(
            session=FakeSession(),press_cycle=FakeCycle(),
            execution_mode=SimpleNamespace(value="FULL_CYCLE"),
            section=section(2.0),pose=WorkpiecePose2D(
                rotation_deg=-45,shift=(2.0,-3.0)),
            tooling=FakeTooling(),dt_s=0.025,timestamp_s=100.0,
        )
        self.assertEqual(row["punch_tip_machine"],[0.0,12.0])
        self.assertEqual(row["die_v_center"],[0.0,-2.0])
        self.assertEqual(row["angles_deg"]["B01"],90.0)
        self.assertIn("fixed_polygon",row["sheet_components"])
        self.assertIn("other_panels",row)
        self.assertEqual(row["active_bend_id"],"B01")
        self.assertEqual(row["elapsed_dt_s"],0.025)
        json.dumps(row,allow_nan=False)

    def test_jumps_and_switches_are_detected_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder=MotionTraceRecorder(tmp,jump_mm=2.0)
            path=recorder.start()
            session=FakeSession()
            cycle=FakeCycle()
            for i,shift in enumerate((0.0,0.1,32.0)):
                if i==2:
                    session.active_bend_id="B02"
                    session.active_bend_index=1
                    cycle.phase=SimpleNamespace(value="APPROACH")
                recorder.record(make_frame_record(
                    session=session,press_cycle=cycle,
                    execution_mode=SimpleNamespace(value="FULL_CYCLE"),
                    section=section(shift),pose=WorkpiecePose2D(),
                    dt_s=0.025,
                ))
            summary=recorder.stop("PLAYBACK_COMPLETED")
            self.assertTrue(path.exists())
            self.assertTrue(summary.exists())
            data=json.loads(Path(summary).read_text(encoding="utf-8"))
            self.assertEqual(data["frames"],3)
            self.assertEqual(data["counts"]["POSITION_JUMP"],1)
            self.assertEqual(data["counts"]["BEND_SWITCH"],1)
            self.assertEqual(data["counts"]["PHASE_CHANGE"],1)
            rows=[json.loads(row) for row in path.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(sum(r["kind"]=="FRAME" for r in rows),3)
            self.assertTrue(any(
                r.get("event")=="POSITION_JUMP" for r in rows
            ))

    def test_no_jump_for_small_continuous_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace=MotionTraceRecorder(tmp,jump_mm=2.0)
            trace.start()
            session=FakeSession()
            for i in range(50):
                trace.record(make_frame_record(
                    session=session,press_cycle=FakeCycle(),
                    execution_mode=SimpleNamespace(value="FULL_CYCLE"),
                    section=section(i*0.1),pose=WorkpiecePose2D(),
                    dt_s=0.025,
                ))
            summary=json.loads(trace.stop().read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"].get("POSITION_JUMP",0),0)

    def test_rejects_invalid_trace_configuration(self):
        with self.assertRaises(ValueError):
            MotionTraceRecorder("unused",jump_mm=float("nan"))
        with self.assertRaises(RuntimeError):
            MotionTraceRecorder("unused").record({"kind":"FRAME"})


if __name__ == "__main__":
    unittest.main()
