"""Regression: a visual replay must not contradict saved native OCC evidence.

Runs headlessly, with no FreeCAD, Qt or OCC installed. Physical equivalence
across all phases still requires integration tests in the FreeCAD runtime.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.workpiece_placement_validation_3d import (
    compare_native_placement_matrices,
)

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "freecad_smbs" / "commands" / "pressbrake_2d.py"


def method(name):
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    matches = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == name]
    if len(matches) != 1:
        raise AssertionError(f"expected one GUI method {name}, found {len(matches)}")
    return matches[0]


def source(name):
    return ast.get_source_segment(GUI.read_text(encoding="utf-8"), method(name))


class PoseNumericsTests(unittest.TestCase):
    def setUp(self):
        self.expected = SimpleNamespace(
            rotation=((1., 0., 0.), (0., -1., 0.), (0., 0., -1.)),
            translation=(14.0, -325.253, 1.5),
        )
        self.sampled = {
            "rotation": [list(row) for row in self.expected.rotation],
            "translation": list(self.expected.translation),
        }

    def test_equal_native_and_preview_machine_poses(self):
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "MATCH",
        )

    def test_small_floating_point_noise_is_allowed(self):
        self.sampled["translation"][0] += 1e-7
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "MATCH",
        )

    def test_wrong_axis_or_side_is_not_accepted(self):
        self.sampled["translation"][1] *= -1.0
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "POSE_MISMATCH",
        )

    def test_rotation_mismatch_is_not_accepted(self):
        self.sampled["rotation"][0][0] *= -1.0
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "POSE_MISMATCH",
        )

    def test_missing_or_nonfinite_values_are_unverified(self):
        self.sampled.pop("rotation")
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "UNVERIFIED",
        )
        self.sampled = {
            "rotation": [list(row) for row in self.expected.rotation],
            "translation": [0.0, math.nan, 0.0],
        }
        self.assertEqual(
            compare_native_placement_matrices(self.expected, self.sampled)["status"],
            "UNVERIFIED",
        )


class ManualPlaybackContractTests(unittest.TestCase):
    def test_manual_playback_does_not_discard_matching_occ_report(self):
        body = source("simulate_manual_order_unverified")
        self.assertIn("_matching_manual_occ_report", body)
        self.assertIn("CycleExecutionMode.FULL_CYCLE", body)
        self.assertIn("CycleExecutionMode.BEND_ONLY", body)
        self.assertIn("matched_occ_report is None", body)

    def test_native_collision_snapshot_stops_playback(self):
        body = source("advance")
        self.assertIn("_native_collision_for_playback", body)
        self.assertIn("_collision_display_active = True", body)
        self.assertIn("MANUAL_PLAYBACK_HALTED_AT_NATIVE_COLLISION", body)

    def test_native_snapshot_uses_canonical_placement(self):
        body = source("_update_3d_preview")
        self.assertIn("compare_native_placement_matrices", body)
        self.assertIn("WorkpiecePlacement3D.compute_placement", body)
        self.assertIn("NATIVE_PREVIEW_POSE_MISMATCH", body)

    def test_displayed_punch_is_not_silently_cut_to_active_bend(self):
        body = source("_update_3d_preview")
        self.assertNotIn("preview_punch_length_mm=preview_length", body)

    def test_conditional_is_preserved_as_distinct_evidence(self):
        body = source("_format_operation_row")
        self.assertIn("CONDITIONAL", body)
        self.assertIn("_manual_3d_verdicts", body)


if __name__ == "__main__":
    unittest.main()
