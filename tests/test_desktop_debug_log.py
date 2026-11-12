"""One continuously updated Desktop JSON, crash recovery, and native 3D samples."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smbs_core.kinematics2d import developer_debug_log as debug


class DesktopDebugLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "Desktop" / "SMBS_debug_log.json"

    def test_valid_single_json_after_every_append_and_process_restart(self):
        journal = debug.DesktopDebugJournal(self.path)
        snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["schema_version"],
                         "smbs.desktop_developer_debug.v1")
        self.assertEqual(snapshot["events"][0]["event"],
                         "SMBS_SESSION_STARTED")
        self.assertTrue(journal.emit("OPERATOR_UI_ACTION", control="Manual"))
        self.assertTrue(journal.emit(
            "MANUAL_NATIVE_BEND_RESULT",
            bend={"status": "COLLISION", "bend_id": "B03",
                  "first_blocker": {"phase": "BENDING", "tool": "DIE",
                                    "die_overlap_mm3": 7.53}},
        ))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["events"][-1]["data"]["bend"]["bend_id"], "B03")
        self.assertEqual(saved["events"][-1]["data"]["bend"]["first_blocker"]["tool"],
                         "DIE")
        self.assertEqual(len(saved["events"]), 3)

        second_session = debug.DesktopDebugJournal(self.path)
        self.assertEqual(second_session._next, 5)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["events"]), 4)
        self.assertEqual(saved["events"][-1]["sequence"], 4)
        self.assertNotEqual(saved["events"][0]["session_id"],
                            saved["events"][-1]["session_id"])
        self.assertEqual(
            sorted(ev["sequence"] for ev in saved["events"]), [1, 2, 3, 4],
        )

    def test_recover_interrupted_json_tail_without_losing_complete_events(self):
        journal = debug.DesktopDebugJournal(self.path)
        journal.emit("IMPORTANT_CAD_RESULT", bend_id="B03")
        with self.path.open("r+b") as fh:
            fh.seek(-len(debug._TAIL), 2)
            fh.write(b',\n{"event":"INCOMPLETE"')
            fh.truncate()
        recovered = debug.DesktopDebugJournal(self.path)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        names = [ev["event"] for ev in saved["events"]]
        self.assertIn("IMPORTANT_CAD_RESULT", names)
        self.assertIn("RECOVERED_INTERRUPTED_JOURNAL", names)
        self.assertEqual(names[-1], "SMBS_SESSION_STARTED")
        self.assertEqual(recovered.last_error, None)

    def test_nonfinite_fields_replaced_with_readable_non_nan_values(self):
        journal = debug.DesktopDebugJournal(self.path)
        self.assertTrue(journal.emit(
            "GEOMETRY_FAULT", distance=float("inf"), volume=float("nan"),
            positions=(1.2, 3.4), raw=Path("selected.FCStd"),
        ))
        value = json.loads(self.path.read_text(encoding="utf-8"))["events"][-1]["data"]
        self.assertEqual(value["distance"], "inf")
        self.assertEqual(value["volume"], "nan")
        self.assertEqual(value["raw"], "selected.FCStd")
        self.assertEqual(value["positions"], [1.2, 3.4])

    def test_windows_redirected_onedrive_desktop_fallback(self):
        home = self.path.parent.parent
        onedrive = home / "OneDrive"
        desktop = onedrive / "Desktop"
        desktop.mkdir(parents=True)
        chosen = debug.desktop_directory(
            home, windows=True, environ={"OneDrive": str(onedrive)},
        )
        self.assertEqual(chosen, desktop)

    def test_every_native_d6_phase_and_collision_sample_is_illustrated(self):
        punch = SimpleNamespace(
            status=SimpleNamespace(value="PENETRATION"),
            overlap_volume_mm3=13.7, distance_mm=0.0,
            tool_label="PUNCH", reason="local overlap",
        )
        die = SimpleNamespace(
            status=SimpleNamespace(value="CLEAR"),
            overlap_volume_mm3=0.0, distance_mm=4.5,
            tool_label="DIE", reason="clear",
        )
        state = SimpleNamespace(
            path_u=0.6, phase="BENDING", active_bend_id="B03",
            signed_angle_deg=67.0, ram_z_mm=-2.7,
            support_plane_zm=-1.8, is_safe=False,
            is_conditional_safe=False,
            collision_report=SimpleNamespace(
                status=SimpleNamespace(value="PENETRATION"),
                punch_result=punch, die_result=die,
            ),
            discontinuity_reason="",
        )
        phase = SimpleNamespace(
            phase_name="BENDING",
            evidence_status=SimpleNamespace(value="DISCRETE_SAMPLES_REJECTED"),
            is_safe=False, is_conditional=False,
            unresolved_intervals=[], uninspected_intervals=[],
            discontinuities=[], collision_queries_count=7,
            max_punch_overlap_volume_mm3=13.7,
            max_die_overlap_volume_mm3=0.0, elapsed_ms=1200,
            diagnostics=["exact geometry branch", "collision"],
            states=[state],
        )
        result = SimpleNamespace(
            bend_id="B03", evidence_status=phase.evidence_status,
            continuous_certification_status=SimpleNamespace(
                value="NOT_CONTINUOUSLY_CERTIFIED",
            ),
            ram_cycle_positions_mm={"retract_end_z_mm": 6.5},
            total_states_evaluated=7, total_collision_queries=7,
            elapsed_ms=1200, diagnostics=["BENDING failed"],
            phase_results=[phase],
        )
        native = debug.summarize_native_trajectory(result)
        journal = debug.DesktopDebugJournal(self.path)
        journal.emit("MANUAL_NATIVE_BEND_TRAJECTORY", native_d6_samples=native)
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        sample = loaded["events"][-1]["data"]["native_d6_samples"]["phases"][0]["samples"][0]
        self.assertEqual(sample["phase"], "BENDING")
        self.assertEqual(sample["punch"]["overlap_volume_mm3"], 13.7)
        self.assertEqual(sample["die"]["distance_mm"], 4.5)
        self.assertEqual(sample["ram_z_mm"], -2.7)
        self.assertEqual(sample["angle_deg"], 67.0)

    def test_occ_common_bbox_is_exact_and_does_not_rerun_boolean(self):
        from smbs_core.kinematics2d.native_collision_3d import (
            CollisionClass, check_pair_clearance,
        )

        def bbox(xmin, xmax, ymin, ymax, zmin, zmax):
            return SimpleNamespace(
                XMin=xmin, XMax=xmax, YMin=ymin,
                YMax=ymax, ZMin=zmin, ZMax=zmax,
            )

        original_box = bbox(0, 100, -10, 10, -20, 20)
        intersection_box = bbox(32.5, 35.0, -2.0, 1.0, -3.5, -1.2)
        shape = SimpleNamespace(
            isNull=lambda: False, isValid=lambda: True,
            isClosed=lambda: True, Volume=1000, BoundBox=original_box,
        )
        common = SimpleNamespace(
            isNull=lambda: False, isValid=lambda: True,
            Volume=3.25, BoundBox=intersection_box,
        )
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common,
        ) as occ, patch(
            "smbs_core.kinematics2d.native_collision_3d."
            "_compute_exact_distance_with_pruning",
            return_value=0.0,
        ):
            result = check_pair_clearance(shape, shape, tool_label="PUNCH")
        self.assertEqual(result.status, CollisionClass.PENETRATION)
        self.assertAlmostEqual(result.overlap_volume_mm3, 3.25)
        self.assertEqual(result.overlap_bbox_xyz_mm, {
            "xmin": 32.5, "xmax": 35.0,
            "ymin": -2.0, "ymax": 1.0,
            "zmin": -3.5, "zmax": -1.2,
        })
        occ.assert_called_once()  # no second slow OCC boolean for location

    def test_no_bbox_means_unknown_location_not_false_zeroes(self):
        from smbs_core.kinematics2d.native_collision_3d import (
            _evaluate_common_volume,
        )
        common = SimpleNamespace(
            isNull=lambda: False,
            isValid=lambda: True, Volume=5.3,
        )
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common,
        ):
            self.assertEqual(
                _evaluate_common_volume(object(), object(), include_bbox=True),
                (5.3, None),
            )

    def test_default_log_filename_and_no_network_or_async_export(self):
        self.assertEqual(
            (debug.desktop_directory(self.path.parent.parent) /
             "SMBS_debug_log.json").name,
            "SMBS_debug_log.json",
        )
        src = (Path(__file__).resolve().parents[1]
               / "freecad_smbs/commands/pressbrake_2d.py"
               ).read_text(encoding="utf-8")
        self.assertIn('def _install_automatic_debug_ui_signals(', src)
        self.assertIn('"OPERATOR_UI_ACTION"', src)
        self.assertIn('"PREVIEW_GEOMETRY_FRAME"', src)
        self.assertIn('"_trace_event(self, name, **fields)"' if False
                      else 'def _trace_event(self, name, **fields):', src)
        core = (Path(__file__).resolve().parents[1]
                / "smbs_core/kinematics2d/manual_physical_validation.py"
               ).read_text(encoding="utf-8")
        self.assertIn('"MANUAL_NATIVE_BEND_TRAJECTORY"', core)
        self.assertIn('"PUNCH_DIE_NATIVE_RAM_SAMPLE"', core)


if __name__ == "__main__":
    unittest.main()
