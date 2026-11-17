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
from smbs_core.kinematics2d.native_assisted_retract import (
    select_assisted_retract_inspection, classify_punch_intersection_span,
)
from smbs_core.kinematics2d.preview_mesh_3d import build_tool_preview_faces

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


class AlternativeRetractEvidenceTests(unittest.TestCase):
    def test_alt_180_retract_is_probeable_after_primary_approach(self):
        primary = {"bend_id": "B02", "first_blocker": {
            "phase": "APPROACH", "tool": "PUNCH",
        }, "retract": {"status": "NOT_EVALUATED"}}
        alternate = {"bend_id": "B02", "reverse_axis": True,
                     "alternative_only": True, "first_blocker": {
                         "phase": "RETRACT", "tool": "PUNCH",
                         "sampled_machine_pose": {"ram_z_mm": 1.503658},
                     }, "retract": {"start_z_mm": -3.492685,
                                   "end_z_mm": 6.5}}
        self.assertIs(
            select_assisted_retract_inspection(primary, alternate),
            alternate,
        )

    def test_non_retract_alternative_not_eligible(self):
        self.assertIsNone(select_assisted_retract_inspection(
            {"first_blocker": {"phase": "APPROACH"}},
            {"first_blocker": {"phase": "BENDING"}},
        ))

    def test_full_width_interference_is_central_not_end_zone(self):
        info = {"punch_span_xm": [-146.5, 146.5]}
        blocker = {"component_attribution": {"hits": [
            {"component_id": "panel:P03", "tool": "PUNCH",
             "overlap_bbox_xyz_mm": {"xmin": -146.5, "xmax": 146.5}},
            {"component_id": "panel:P09", "tool": "PUNCH",
             "overlap_bbox_xyz_mm": {"xmin": 140., "xmax": 146.5}},
        ]}}
        d = classify_punch_intersection_span(blocker, info, 10.0)
        self.assertEqual(d["status"], "CENTRAL_PUNCH_INTERFERENCE")
        self.assertIn("panel:P03", d["central_hits"])
        self.assertIn("panel:P09", d["end_zone_hits"])

    def test_end_zone_only_bbox_cannot_certify_clear(self):
        d = classify_punch_intersection_span(
            {"component_attribution": {"hits": [
                {"component_id": "panel:P09", "tool": "PUNCH",
                 "overlap_bbox_xyz_mm": {"xmin": 140, "xmax": 146.5}},
            ]}},
            {"punch_span_xm": [-146.5, 146.5]}, 10.0,
        )
        self.assertEqual(d["status"], "END_ZONE_ONLY_BBOX_UNVERIFIED")


class ManualPlaybackContractTests(unittest.TestCase):
    def test_inspected_bend_length_resizes_both_virtual_tools_only(self):
        def section(**_kwargs):
            return ((0, 0), (1, 0), (0, 1)), ()

        tooling = SimpleNamespace(
            visible=True, ram_z_mm=0.0, active_bend_length_mm=50.0,
            match_active_bend_length=True,
            punch=SimpleNamespace(tool_id="punch", length_mm=100.0),
            die=SimpleNamespace(tool_id="die", length_mm=200.0),
            punch_polygons_machine=section, die_polygons_machine=section,
            punch_section_offsets_xm=(), die_section_offsets_xm=(),
        )

        def extents():
            faces = build_tool_preview_faces(tooling, active_bend_length_mm=20.0)
            return {
                face.kind: set(vertex[1] for frame in faces if frame.kind == face.kind
                               for vertex in frame.vertices)
                for face in faces if face.kind.endswith("_cap")
            }

        self.assertEqual(extents(), {"punch_cap": {-10, 10}, "die_cap": {-10, 10}})
        tooling.match_active_bend_length = False
        self.assertEqual(extents(), {"punch_cap": {-50, 50}, "die_cap": {-100, 100}})

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

    def test_replay_rejects_stale_session_tooling_or_orientation(self):
        body = source("_matching_manual_occ_report")
        self.assertIn("native_tooling_identity", body)
        self.assertIn("_current_native_tooling_identity", body)
        self.assertIn("requested_bends", body)

    def test_3d_status_shows_sampled_occ_verdict(self):
        body = source("_update_3d_preview")
        self.assertIn("current_verdict", body)
        self.assertIn("OCC: {} (SAMPLED)", body)

    def test_second_native_orientation_is_retained_for_retract_probe(self):
        check = source("_validate_export_manual_order")
        self.assertIn("alternative_collision_reports", check)
        self.assertIn("MANUAL_ALTERNATIVE_COLLISION_RETAINED", check)
        self.assertIn("select_assisted_retract_inspection", check)

    def test_alt_retract_cannot_silently_activate_normal_preview(self):
        check = source("_inspect_assisted_retract")
        self.assertIn("alternative_only", check)
        self.assertIn("orientation_matches", check)
        self.assertIn("_last_assisted_lift_inspection", check)

    def test_tooling_page_opens_the_real_segment_library(self):
        # Regression: old Tool Library dialog did not contain the segment
        # inventory editor. All entry points must reach ToolingPanel.
        gui = GUI.read_text(encoding="utf-8")
        panel = (
            ROOT / "freecad_smbs" / "ui" / "tooling_panel.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.open_full_tooling_library", gui)
        self.assertIn("def open_full_tooling_library(self)", gui)
        self.assertIn("ToolingPanel(library, parent=self)", gui)
        self.assertIn("Рассчитать набор сегментов", gui)
        self.assertIn("Сегменты и количество", panel)
        self.assertIn("def _on_edit_segments(self)", panel)
        self.assertNotIn("def open_tool_library_dialog(self)", gui)

    def test_conditional_is_preserved_as_distinct_evidence(self):
        body = source("_format_operation_row")
        self.assertIn("CONDITIONAL", body)
        self.assertIn("_manual_3d_verdicts", body)


if __name__ == "__main__":
    unittest.main()
