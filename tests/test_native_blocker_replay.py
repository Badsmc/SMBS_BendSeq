"""Pure Python fail-closed contracts for single-pose native blocker replay."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from tools.replay_native_component_blocker import (
    BlockerReplayError,
    select_blocker_evidence,
    verify_source_bends,
    verify_sample_pose,
)


def event(name, data, session="S1"):
    return {"event": name, "data": data, "session_id": session}


def fixture():
    return {
        "schema_version": "smbs.desktop_developer_debug.v1",
        "events": [
            event("PART_LOAD_REQUEST", {
                "document_path": "C:/demo/ref.FCStd",
                "selection": [{"name": "Body_Unfold"}],
            }),
            event("PART_GEOMETRY_LOADED", {
                "fixed_panel": "P05",
                "bends": [
                    {"id": "B02", "angle_deg": 90.0,
                     "axis_xyz": [[0., 0., 0.], [100., 0., 0.]]},
                ],
            }),
            event("MANUAL_NATIVE_CHECK_STARTED", {
                "bend_order": ["B02"],
                "punch_file": "C:/tools/punch.FCStd",
                "die_file": "C:/tools/die.FCStd",
                "virtual_tool_length": True,
                "adaptive_config": {"volume_tolerance_mm3": 1e-4},
            }),
            event("MANUAL_NATIVE_CHECK_FINISHED", {
                "status": "COLLISION", "reverse_axis_by_bend": {"B02": True},
                "steps": [{
                    "bend_id": "B02", "status": "COLLISION",
                    "native_status": "DISCRETE_SAMPLES_REJECTED",
                    "first_blocker": {
                        "phase": "RETRACT", "tool": "PUNCH",
                        "sampled_onset_bracket": {
                            "first_sampled_blocking_u": 0.15625,
                        },
                        "sampled_machine_pose": {
                            "bend_id": "B02", "phase": "RETRACT",
                            "signed_angle_deg": 90., "ram_z_mm": -1.931327,
                            "support_plane_zm": -2.4926,
                            "completed_angles": {"B07": 90.},
                            "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                            "translation": [5, 6, 7],
                        },
                        "component_attribution": {
                            "hits": [
                                {"component_id": "panel:P09", "tool": "PUNCH",
                                 "overlap_volume_mm3": 500.926},
                                {"component_id": "bend_zone:B09", "tool": "PUNCH",
                                 "overlap_volume_mm3": 2.752},
                            ],
                        },
                    },
                }],
            }),
        ],
    }


class BlockerReplayContractTests(unittest.TestCase):
    def test_selects_exact_blocking_pose_and_all_named_hits(self):
        got = select_blocker_evidence(fixture(), "B02")
        self.assertEqual(got["first_sampled_blocking_u"], 0.15625)
        self.assertEqual(got["part_path"], "C:/demo/ref.FCStd")
        self.assertEqual(got["unfold_name"], "Body_Unfold")
        self.assertTrue(got["reverse_axis"])
        self.assertTrue(got["virtual_tool_length"])
        self.assertEqual(set(got["attributed_hits"]), {"P09", "bend_zone:B09"})
        self.assertEqual(got["attributed_hits"]["bend_zone:B09"]["volume_mm3"], 2.752)
        self.assertEqual(got["pose"]["phase"], "RETRACT")

    def test_unknown_or_noncolliding_bend_has_no_replay(self):
        with self.assertRaises(BlockerReplayError):
            select_blocker_evidence(fixture(), "B09")
        d = fixture()
        d["events"][-1]["data"]["steps"][0]["status"] = "CONDITIONAL"
        with self.assertRaises(BlockerReplayError):
            select_blocker_evidence(d, "B02")

    def test_mismatched_pose_cannot_be_replayed_as_a_native_hit(self):
        d = fixture()
        d["events"][-1]["data"]["steps"][0]["first_blocker"][
            "sampled_machine_pose"
        ]["phase"] = "BENDING"
        with self.assertRaisesRegex(BlockerReplayError, "phase_mismatch"):
            select_blocker_evidence(d, "B02")

    def test_invalid_original_attribution_fails_closed(self):
        d = fixture()
        d["events"][-1]["data"]["steps"][0]["first_blocker"][
            "component_attribution"
        ]["hits"].append({
            "component_id": "panel:P09", "tool": "PUNCH",
            "overlap_volume_mm3": 5.0,
        })
        with self.assertRaisesRegex(BlockerReplayError, "duplicate_hit"):
            select_blocker_evidence(d, "B02")

    def test_noncolliding_finished_run_cannot_authorize_replay(self):
        d = fixture()
        d["events"][-1]["data"]["status"] = "UNVERIFIED"
        with self.assertRaises(BlockerReplayError):
            select_blocker_evidence(d, "B02")

    def test_nan_bend_angle_is_never_verified(self):
        bends = {"B02": SimpleNamespace(
            angle_deg=float("nan"),
            axis_xyz=((0., 0., 0.), (100., 0., 0.)),
        )}
        recorded = fixture()["events"][1]["data"]["bends"]
        with self.assertRaisesRegex(BlockerReplayError, "source_bend_angle_changed"):
            verify_source_bends(bends, recorded)

    def test_missing_axis_coordinate_is_not_ignored_by_zip(self):
        bends = {"B02": SimpleNamespace(
            angle_deg=90.0,
            axis_xyz=((0., 0.), (100., 0.)),
        )}
        recorded = fixture()["events"][1]["data"]["bends"]
        with self.assertRaisesRegex(BlockerReplayError, "source_bend_axis_missing"):
            verify_source_bends(bends, recorded)

    def test_placement_must_match_native_recomputation(self):
        pose = {
            "rotation": [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
            "translation": [5., 6., 7.],
        }
        actual = SimpleNamespace(
            rotation=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
            translation=(5., 6., 7.),
        )
        self.assertEqual(verify_sample_pose(actual, pose)["translation_max_abs_error_mm"], 0)
        changed = copy.deepcopy(pose)
        changed["translation"][0] = 5.1
        with self.assertRaisesRegex(BlockerReplayError, "native_placement_mismatch"):
            verify_sample_pose(actual, changed)

    def test_changed_source_bend_axes_or_angles_rejected(self):
        bends = {"B02": SimpleNamespace(
            angle_deg=90.0,
            axis_xyz=((0., 0., 0.), (100., 0., 0.)),
        )}
        recorded = fixture()["events"][1]["data"]["bends"]
        verify_source_bends(bends, recorded)
        altered = copy.deepcopy(recorded)
        altered[0]["axis_xyz"][1][0] = 100.02
        with self.assertRaisesRegex(BlockerReplayError, "axis_changed"):
            verify_source_bends(bends, altered)


if __name__ == "__main__":
    unittest.main()
