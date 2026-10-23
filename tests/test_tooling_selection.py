"""
test_tooling_selection.py - Unit and DP tests for select_tooling (Req 4 Phase E, S.30b).
"""

import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.clamp_validator import ClampConfig
from smbs_core.geometry_backend.io.tooling_catalog import ToolDescriptor
from smbs_core.tooling.selection import ToolingSelectionError, select_tooling


class TestToolingSelection(unittest.TestCase):
    def setUp(self):
        # Clamps: 3 clamps at positions 75, 275, 475 via from_defaults
        # width_mm=150, gap=50. Covers [0..150], [200..350], [400..550].
        # For min_clamps_under_punch=2, chain must span across at least 2 clamps (e.g. length >= 200mm)
        self.clamps_config = ClampConfig.from_defaults(positions_mm=(75.0, 275.0, 475.0))
        self.segment_catalog = [100.0, 50.0, 20.0, 15.0, 10.0]

        # Punch A: radius 1.0, 88 deg
        self.punch_a = ToolDescriptor(
            tool_id="punch_a",
            kind="punch",
            fcstd_path=Path("/tmp/punch_a.FCStd"),
            sidecar_path=None,
            has_sidecar=True,
            metadata={"radius_mm": 1.0, "angle_deg": 88.0, "height_mm": 100.0}
        )
        # Punch B: radius 1.5, 88 deg
        self.punch_b = ToolDescriptor(
            tool_id="punch_b",
            kind="punch",
            fcstd_path=Path("/tmp/punch_b.FCStd"),
            sidecar_path=None,
            has_sidecar=True,
            metadata={"radius_mm": 1.5, "angle_deg": 88.0, "height_mm": 100.0}
        )

        # Die A: V=12 (matches T=2.0 -> 6*2=12), 88 deg
        self.die_a = ToolDescriptor(
            tool_id="die_a",
            kind="die",
            fcstd_path=Path("/tmp/die_a.FCStd"),
            sidecar_path=None,
            has_sidecar=True,
            metadata={"v_width_mm": 12.0, "angle_deg": 88.0, "height_mm": 60.0}
        )
        # Die B: V=16, 88 deg
        self.die_b = ToolDescriptor(
            tool_id="die_b",
            kind="die",
            fcstd_path=Path("/tmp/die_b.FCStd"),
            sidecar_path=None,
            has_sidecar=True,
            metadata={"v_width_mm": 16.0, "angle_deg": 88.0, "height_mm": 60.0}
        )

        # Standard bend dicts: T=2.0 -> target V=12
        # bend_1: length=262.5 -> target_length = 262.5 - 12.5 = 250.0 (covers clamps 1 and 2, valid!)
        # bend_2: length=312.5 -> target_length = 312.5 - 12.5 = 300.0 (covers clamps 1 and 2, valid!)
        self.bends_map = {
            "bend_1": {
                "length_mm": 262.5,
                "radius_mm": 1.0,
                "angle_deg": 88.0,
                "thickness_mm": 2.0
            },
            "bend_2": {
                "length_mm": 312.5,
                "radius_mm": 1.0,
                "angle_deg": 88.0,
                "thickness_mm": 2.0
            }
        }
        self.candidate_sequence = [
            {"step": 1, "bend_id": "bend_1"},
            {"step": 2, "bend_id": "bend_2"}
        ]

    def test_happy_path_valid_two_bends(self):
        """Case 1: Happy path: 2 bends, 1 punch, 1 die, clamps valid -> 0 tool changes."""
        res = select_tooling(
            candidate_sequence=self.candidate_sequence,
            bends_map=self.bends_map,
            clamps_config=self.clamps_config,
            available_punches=[self.punch_a],
            available_dies=[self.die_a],
            segment_catalog=self.segment_catalog,
            objective="MIN_TOOL_CHANGES"
        )
        self.assertEqual(res["objective"], "MIN_TOOL_CHANGES")
        self.assertEqual(len(res["assignments"]), 2)
        self.assertEqual(res["total_tool_changes"], 0)
        self.assertEqual(res["assignments"][0]["punch_id"], "punch_a")
        self.assertEqual(res["assignments"][0]["die_id"], "die_a")
        self.assertTrue(res["assignments"][0]["clamp_validation"]["valid"])

    def test_clamps_config_none_raises_tooling_selection_error(self):
        """Case 2: clamps_config=None raises ToolingSelectionError."""
        with self.assertRaises(ToolingSelectionError) as ctx:
            select_tooling(
                candidate_sequence=self.candidate_sequence,
                bends_map=self.bends_map,
                clamps_config=None,
                available_punches=[self.punch_a],
                available_dies=[self.die_a],
                segment_catalog=self.segment_catalog
            )
        self.assertIn("enforcement requires clamps_config", str(ctx.exception).lower())

    def test_empty_segment_catalog_raises(self):
        """Case 3: Empty segment_catalog raises ToolingSelectionError."""
        with self.assertRaises(ToolingSelectionError):
            select_tooling(
                candidate_sequence=self.candidate_sequence,
                bends_map=self.bends_map,
                clamps_config=self.clamps_config,
                available_punches=[self.punch_a],
                available_dies=[self.die_a],
                segment_catalog=[]
            )

    def test_non_positive_segment_catalog_raises(self):
        """Case 4: Non-positive value in segment_catalog raises ToolingSelectionError."""
        with self.assertRaises(ToolingSelectionError):
            select_tooling(
                candidate_sequence=self.candidate_sequence,
                bends_map=self.bends_map,
                clamps_config=self.clamps_config,
                available_punches=[self.punch_a],
                available_dies=[self.die_a],
                segment_catalog=[100.0, 0.0, 50.0]
            )

    def test_invalid_objective_raises(self):
        """Case 5: Unsupported objective raises ToolingSelectionError."""
        with self.assertRaises(ToolingSelectionError):
            select_tooling(
                candidate_sequence=self.candidate_sequence,
                bends_map=self.bends_map,
                clamps_config=self.clamps_config,
                available_punches=[self.punch_a],
                available_dies=[self.die_a],
                segment_catalog=self.segment_catalog,
                objective="FASTEST"
            )

    def test_missing_bend_in_bends_map_raises(self):
        """Case 6: Candidate sequence bend absent from bends_map raises ToolingSelectionError."""
        seq = [{"step": 1, "bend_id": "missing_bend"}]
        with self.assertRaises(ToolingSelectionError) as ctx:
            select_tooling(
                candidate_sequence=seq,
                bends_map=self.bends_map,
                clamps_config=self.clamps_config,
                available_punches=[self.punch_a],
                available_dies=[self.die_a],
                segment_catalog=self.segment_catalog
            )
        self.assertIn("missing_bend", str(ctx.exception))

    def test_punch_metadata_missing_radius_skipped(self):
        """Case 7: Punch without radius_mm in metadata is skipped -> warnings and empty assignments."""
        bad_punch = ToolDescriptor(
            tool_id="bad_punch",
            kind="punch",
            fcstd_path=Path("/tmp/p.FCStd"),
            sidecar_path=None,
            has_sidecar=False,
            metadata={"angle_deg": 88.0}
        )
        res = select_tooling(
            candidate_sequence=self.candidate_sequence,
            bends_map=self.bends_map,
            clamps_config=self.clamps_config,
            available_punches=[bad_punch],
            available_dies=[self.die_a],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 0)
        self.assertTrue(any("no valid" in w for w in res["warnings"]))

    def test_die_metadata_missing_v_width_skipped(self):
        """Case 8: Die without v_width_mm in metadata is skipped -> warnings and empty assignments."""
        bad_die = ToolDescriptor(
            tool_id="bad_die",
            kind="die",
            fcstd_path=Path("/tmp/d.FCStd"),
            sidecar_path=None,
            has_sidecar=False,
            metadata={"angle_deg": 88.0}
        )
        res = select_tooling(
            candidate_sequence=self.candidate_sequence,
            bends_map=self.bends_map,
            clamps_config=self.clamps_config,
            available_punches=[self.punch_a],
            available_dies=[bad_die],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 0)
        self.assertTrue(any("no valid" in w for w in res["warnings"]))

    def test_clamp_invalid_rejects_candidates(self):
        """Case 9: ClampConfig with empty positions -> validate_punch_chain fails -> no assignments."""
        empty_clamp_config = ClampConfig.from_defaults(positions_mm=())
        res = select_tooling(
            candidate_sequence=self.candidate_sequence,
            bends_map=self.bends_map,
            clamps_config=empty_clamp_config,
            available_punches=[self.punch_a],
            available_dies=[self.die_a],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 0)
        self.assertTrue(any("no valid" in w for w in res["warnings"]))

    def test_bend_length_too_short_warning(self):
        """Case 10: Bend length <= 12.5mm produces warning and no assignment."""
        short_bends = {
            "bend_short": {
                "length_mm": 10.0,
                "radius_mm": 1.0,
                "angle_deg": 88.0,
                "thickness_mm": 2.0
            }
        }
        seq = [{"step": 1, "bend_id": "bend_short"}]
        res = select_tooling(
            candidate_sequence=seq,
            bends_map=short_bends,
            clamps_config=self.clamps_config,
            available_punches=[self.punch_a],
            available_dies=[self.die_a],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 0)
        self.assertTrue(any("too short" in w for w in res["warnings"]))

    def test_dp_minimizes_tool_changes(self):
        """Case 11: DP chooses punch_a for both bends when punch_b only fits bend_2 -> 0 changes."""
        # bend_1: R=1.0 (punch_a tip 1.0 <= 1.0*1.2 fits; punch_b tip 1.5 > 1.2 fails)
        # bend_2: R=1.5 (both punch_a tip 1.0 <= 1.8 and punch_b tip 1.5 <= 1.8 fit)
        bends_map = {
            "bend_1": {"length_mm": 262.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.0},
            "bend_2": {"length_mm": 312.5, "radius_mm": 1.5, "angle_deg": 88.0, "thickness_mm": 2.0}
        }
        res = select_tooling(
            candidate_sequence=self.candidate_sequence,
            bends_map=bends_map,
            clamps_config=self.clamps_config,
            available_punches=[self.punch_a, self.punch_b],
            available_dies=[self.die_a],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 2)
        # DP selects punch_a for both bends to get 0 tool changes
        self.assertEqual(res["assignments"][0]["punch_id"], "punch_a")
        self.assertEqual(res["assignments"][1]["punch_id"], "punch_a")
        self.assertEqual(res["total_tool_changes"], 0)

    def test_dp_forces_tool_change_when_necessary(self):
        """Case 12: 3 bends, where bend 2 requires punch_c (different die/punch) -> tool changes."""
        # Die A: V=12 (T=2.0 -> V≈12). Die B: V=16 (T=2.7 -> V≈16.2).
        # bend_1: T=2.0 (only die_a fits)
        # bend_2: T=2.7 (only die_b fits)
        # bend_3: T=2.0 (only die_a fits)
        bends_map = {
            "bend_1": {"length_mm": 262.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.0},
            "bend_2": {"length_mm": 262.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.7},
            "bend_3": {"length_mm": 262.5, "radius_mm": 1.0, "angle_deg": 88.0, "thickness_mm": 2.0}
        }
        seq = [
            {"step": 1, "bend_id": "bend_1"},
            {"step": 2, "bend_id": "bend_2"},
            {"step": 3, "bend_id": "bend_3"}
        ]
        res = select_tooling(
            candidate_sequence=seq,
            bends_map=bends_map,
            clamps_config=self.clamps_config,
            available_punches=[self.punch_a],
            available_dies=[self.die_a, self.die_b],
            segment_catalog=self.segment_catalog
        )
        self.assertEqual(len(res["assignments"]), 3)
        self.assertEqual(res["assignments"][0]["die_id"], "die_a")
        self.assertEqual(res["assignments"][1]["die_id"], "die_b")
        self.assertEqual(res["assignments"][2]["die_id"], "die_a")
        self.assertEqual(res["total_tool_changes"], 2)
        self.assertTrue(res["assignments"][1]["changes_from_previous"]["die_changed"])
        self.assertTrue(res["assignments"][2]["changes_from_previous"]["die_changed"])


if __name__ == "__main__":
    unittest.main()
