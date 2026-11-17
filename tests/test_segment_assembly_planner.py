"""Headless regression for finite, per-tool section stock and clamp assembly."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig, load_clamp_config, save_clamp_config,
)
from smbs_core.kinematics2d.segment_assembly_planner import (
    AssemblyPlanningError,
    read_inventory, write_inventory, validate_inventory,
    plan_pair_for_bend, _unique_segment_orders,
)
from smbs_core.kinematics2d.tooling_assembly import ToolAssembly

PUNCH = [{"length_mm": 150.0, "quantity": 2}]
DIE = [{"length_mm": 100.0, "quantity": 3}]


def clamps(*, gap=20., positions=(-120., 0., 120.)):
    return ClampConfig(
        width_mm=80., gap_between_mm=gap,
        positions_mm=positions, overhang_ratio=.3,
        joint_tolerance_mm=.1, min_clamps_under_punch=2,
        repositionable=False, joints_must_be_on_clamps=True,
        no_gaps_between_punches=True,
    )


class SegmentInventoryTests(unittest.TestCase):
    def test_persistent_per_tool_inventory_preserves_fcstd_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / "my_punch.FCStd"
            tool.write_bytes(b"FCStd unchanged")
            sidecar = tool.with_suffix(".tooling.json")
            sidecar.write_text(json.dumps({
                "id": "my_punch", "kind": "punch",
                "datums": {"working_axis": {"direction": [0, 0, 1]}},
                "metadata": {"angle_deg": 88}
            }), encoding="utf-8")
            write_inventory(tool, PUNCH)
            self.assertEqual(read_inventory(tool), PUNCH)
            self.assertEqual(tool.read_bytes(), b"FCStd unchanged")
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["angle_deg"], 88)
            self.assertIn("working_axis", data["datums"])

    def test_bad_counts_and_duplicate_lengths_rejected(self):
        with self.assertRaises(AssemblyPlanningError):
            validate_inventory([{"length_mm": 100, "quantity": -1}])
        with self.assertRaises(AssemblyPlanningError):
            validate_inventory([{"length_mm": 100, "quantity": 1},
                                {"length_mm": 100, "quantity": 2}])
        with self.assertRaises(AssemblyPlanningError):
            validate_inventory([{"length_mm": 100, "quantity": 1.5}])

    def test_existing_inventory_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / "my_punch.FCStd"
            tool.write_bytes(b"raw")
            self.assertEqual(read_inventory(tool), [])

    def test_multiset_permutations_do_not_explode(self):
        self.assertEqual(list(_unique_segment_orders([100]*12)),
                         [tuple([100]*12)])


class SegmentPlannerTests(unittest.TestCase):
    def test_plan_reports_each_segment_and_clamp_machine_x(self):
        plan = plan_pair_for_bend(PUNCH, DIE, 293., clamps())
        self.assertEqual(plan["status"], "ASSEMBLY_CANDIDATE_UNVERIFIED")
        self.assertEqual(plan["punch_total_mm"], 300)
        self.assertEqual(plan["die_total_mm"], 300)
        self.assertEqual([s["length_mm"] for s in plan["punch_segments"]],
                         [150, 150])
        self.assertEqual([s["length_mm"] for s in plan["die_segments"]],
                         [100, 100, 100])
        self.assertEqual(plan["punch_segments"][0]["start_x_mm"], -150)
        self.assertEqual(plan["punch_segments"][-1]["end_x_mm"], 150)
        self.assertEqual(plan["clamps"]["centers_mm"], [-120., 0., 120.])
        self.assertEqual(plan["clamps"]["gap_between_mm"], 20.)

    def test_cannot_use_more_sections_than_stock(self):
        plan = plan_pair_for_bend([{"length_mm": 150., "quantity": 1}],
                                  DIE, 293., clamps())
        self.assertEqual(plan["status"], "NO_INVENTORY_COMBINATION")

    def test_clamp_gap_blocks_previously_possible_assembly(self):
        plan = plan_pair_for_bend(PUNCH, DIE, 293., clamps(gap=50.))
        self.assertEqual(plan["status"], "NO_CLAMP_COMPATIBLE_ASSEMBLY")

    def test_no_invented_clamps_for_fixed_empty_configuration(self):
        plan = plan_pair_for_bend(PUNCH, DIE, 293.,
                                  clamps(positions=()))
        self.assertEqual(plan["status"], "NO_CLAMP_COMPATIBLE_ASSEMBLY")

    def test_no_silent_short_punch(self):
        plan = plan_pair_for_bend(PUNCH, DIE, 315., clamps())
        self.assertEqual(plan["status"], "NO_INVENTORY_COMBINATION")

    def test_count_generates_a_fixed_clamp_row_after_reload(self):
        config = replace(clamps(gap=40, positions=()), clamp_count=3)
        with tempfile.TemporaryDirectory() as folder:
            save_clamp_config(Path(folder), config)
            config = load_clamp_config(Path(folder))
        self.assertEqual(config.centres_mm, (-120, 0, 120))
        plan = plan_pair_for_bend(PUNCH, DIE, 293., config)
        assembly = ToolAssembly.from_plan(plan)
        self.assertEqual(assembly.punch_spans_xm, ((-150, 0), (0, 150)))
        self.assertEqual(assembly.die_spans_xm, ((-150, -50), (-50, 50), (50, 150)))
        self.assertEqual(assembly.clamp_spans_xm, ((-160, -80), (-40, 40), (80, 160)))
        plan["clamps"]["proposed_reposition"] = True
        with self.assertRaises(ValueError):
            ToolAssembly.from_plan(plan)

    def test_every_punch_segment_needs_some_overlap_with_a_clamp(self):
        config = replace(
            clamps(gap=80, positions=(-65, 65)), width_mm=50, joints_must_be_on_clamps=False
        )
        segments = [{"length_mm": 40, "quantity": 3}]
        plan = plan_pair_for_bend(segments, segments, 120, config, allowance_mm=0)
        self.assertEqual(plan["status"], "NO_CLAMP_COMPATIBLE_ASSEMBLY")

    def test_assembly_can_use_more_than_twelve_stock_segments(self):
        config = replace(clamps(gap=0, positions=()), clamp_count=7, width_mm=150)
        segments = [{"length_mm": 75, "quantity": 13}]
        plan = plan_pair_for_bend(segments, segments, 975, config, allowance_mm=0)
        self.assertEqual(plan["status"], "ASSEMBLY_CANDIDATE_UNVERIFIED")
        self.assertEqual(len(plan["punch_segments"]), 13)


class ToolingLibraryUIContracts(unittest.TestCase):
    def test_sidecars_are_not_reported_as_invalid_tools(self):
        source = (
            Path(__file__).resolve().parents[1] /
            "smbs_core/geometry_backend/io/tooling_catalog.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("skipping non-FCStd file", source)

    def test_refresh_keeps_selected_segment_tool(self):
        source = (
            Path(__file__).resolve().parents[1] /
            "freecad_smbs/ui/tooling_panel.py"
        ).read_text(encoding="utf-8")
        self.assertIn("selected_key = (", source)
        self.assertIn("widget.setCurrentRow(index)", source)
        self.assertIn("self._refresh_lists()", source)


if __name__ == "__main__":
    unittest.main()
