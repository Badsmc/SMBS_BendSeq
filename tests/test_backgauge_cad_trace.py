"""Headless tests: trace integrity, bounded recording and file locations."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from smbs_core.kinematics2d.backgauge_cad_trace import (
    BackgaugeCADTrace, bbox_xyz, fresh_trace_path, point_xyz,
)


class TestBackgaugeCADTrace(unittest.TestCase):
    def test_jsonl_is_machine_readable_after_each_event(self):
        with tempfile.TemporaryDirectory() as td:
            path = fresh_trace_path(Path(td) / "diagnostics")
            sink = BackgaugeCADTrace(path)
            sink.emit(
                "CONTACT_OBSTRUCTED", bend_id="B06",
                obstructing_component="panel:P05", overlap_volume_mm3=18963.98,
            )
            lines = [json.loads(v) for v in path.read_text(
                encoding="utf-8"
            ).splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["event"], "TRACE_STARTED")
            self.assertEqual(lines[1]["event"], "CONTACT_OBSTRUCTED")
            self.assertEqual(lines[1]["bend_id"], "B06")
            self.assertEqual(lines[1]["overlap_volume_mm3"], 18963.98)

    def test_truncation_is_reported_and_file_does_not_grow_forever(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trace.jsonl"
            sink = BackgaugeCADTrace(path, max_events=2)
            for i in range(15):
                sink.emit("CONTACT_PROBE", index=i)
            lines = [json.loads(v) for v in path.read_text(
                encoding="utf-8"
            ).splitlines()]
            self.assertEqual([v["event"] for v in lines], [
                "TRACE_STARTED", "CONTACT_PROBE", "CONTACT_PROBE",
                "TRACE_TRUNCATED",
            ])

    def test_bbox_and_point_are_serializable(self):
        box = SimpleNamespace(XMin=-25, XMax=25, YMin=-35,
                              YMax=0, ZMin=0, ZMax=14)
        sh = SimpleNamespace(BoundBox=box)
        self.assertEqual(bbox_xyz(sh)["xmin"], -25)
        self.assertEqual(bbox_xyz(sh)["zmax"], 14)
        self.assertEqual(point_xyz(SimpleNamespace(x=1,y=2,z=3)),
                         [1,2,3])

    def test_unique_logfile_path_has_jsonl_extension(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertNotEqual(
                fresh_trace_path(td), fresh_trace_path(td)
            )
            self.assertEqual(fresh_trace_path(td).suffix, ".jsonl")


if __name__ == "__main__":
    unittest.main()
