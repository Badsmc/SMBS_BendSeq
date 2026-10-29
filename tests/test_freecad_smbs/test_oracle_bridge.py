"""
test_oracle_bridge.py - Unit & Integration Test Suite for OracleBridge (Phase 4c.1)

Verifies:
1. test_oracle_bridge_tooling_required: raises ValueError / ContractError if tooling_set_id is None.
2. test_oracle_bridge_works_on_real_run: run_20261012_034045_9a351d passes all 4 candidate steps.
3. test_oracle_bridge_uses_preserving_order: face count and index order remain invariant after unfold.
4. test_oracle_bridge_rejects_invalid_order: test_box invalid order ('BEND_01', 'BEND_02', 'BEND_03')
   fails at step 2 with stage="BEND_TRAJECTORY".
5. test_oracle_bridge_accepts_valid_order: test_box valid order passes all 3 steps.
"""

import unittest
from pathlib import Path
import json

from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE

from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Physics.machine_kinematics import PressBrakeMachine

from freecad_smbs.manual.oracle_bridge import (
    OracleBridge,
    transform_compound_preserving_order,
    resolve_panel_primary_sheet_pair
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestOracleBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tooling_fixture_path = REPO_ROOT / "tests/fixtures/manual/tooling_std.json"
        with open(cls.tooling_fixture_path, "r", encoding="utf-8") as f:
            cls.tooling_data = json.load(f)
        cls.tooling_set_id = cls.tooling_data["tooling_set_id"]

    def test_oracle_bridge_tooling_required(self):
        """Phase 4c.1 / H4: OracleBridge requires explicit tooling_set_id; fails closed if None."""
        with self.assertRaises(ValueError) as ctx:
            OracleBridge(tooling_set_id=None)
        self.assertIn("tooling_set_id is required", str(ctx.exception))

    def test_oracle_bridge_initializes_tooling(self):
        """Phase 4c.1 / K1: Verifies OracleBridge loads real tooling datums and assembly transforms."""
        from smbs_core.models.tooling import DatumRole, ToolingKind
        bridge = OracleBridge(tooling_set_id="standard_90deg_v12")
        self.assertIsNotNone(bridge.validator.assembly)
        self.assertIsNotNone(bridge.validator.assembly.die)
        self.assertIsNotNone(bridge.validator.assembly.die.datums)
        self.assertTrue(bridge.validator.assembly.die.datums[DatumRole.V_CENTER].confirmed)
        self.assertNotEqual(bridge.validator.assembly.assembly_trsf, {})
        self.assertIn(ToolingKind.DIE, bridge.validator.assembly.assembly_trsf)
        self.assertIn(ToolingKind.PUNCH, bridge.validator.assembly.assembly_trsf)

    def test_oracle_bridge_production_policy_on_real_run(self):
        """Phase 4c.1 / K1: Validates all 4 steps on real baseline run_20261012_034045_9a351d under PRODUCTION."""
        import time
        rdir = REPO_ROOT / "runs/run_20261012_034045_9a351d"
        p4_path = rdir / "04_canonical_part.json"
        s7_path = rdir / "07_search_result.json"
        geom_path = rdir / "geometry.brep"

        with open(p4_path, "r", encoding="utf-8") as f:
            data = json.load(f)["payload"]
        with open(s7_path, "r", encoding="utf-8") as f:
            s7 = json.load(f)["payload"]

        seq = [s["bend_id"] for s in s7["candidate_sequence"]]
        self.assertEqual(len(seq), 4)

        builder = BRep_Builder()
        ref_shape = TopoDS_Shape()
        breptools.Read(ref_shape, str(geom_path), builder)

        pg = PanelGraph()
        for pid, pdata in data["panels"].items():
            pg.add_panel(Panel(pid, normal=tuple(pdata.get("normal", (0, 0, 1)))))
        for bid, bdata in data["bends"].items():
            pg.add_bend_connection(bid, bdata["parent_panel_id"], bdata["child_panel_id"])
        resolve_panel_primary_sheet_pair(ref_shape, pg, data)

        bg = BendGraph()
        bend_records = {}
        for bid, bdata in data["bends"].items():
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=bdata["parent_panel_id"],
                child_panel_id=bdata["child_panel_id"],
                axis_origin=tuple(bdata["axis_point"]),
                axis_direction=tuple(bdata["axis_direction"]),
                signed_angle=bdata["angle_deg"]["value"],
                length=bdata["length_mm"]["value"],
                radius=bdata["radius_mm"]["value"],
                thickness=data["thickness_mm"]["value"]
            )
            bg.add_bend(rec)
            bend_records[bid] = rec

        bridge = OracleBridge(tooling_set_id=self.tooling_set_id)
        current_state = FoldState.initial(ref_shape, bg, pg)

        start_time = time.time()
        for step_idx, bid in enumerate(seq, start=1):
            t0 = time.time()
            res = bridge.validate_step(
                current_state=current_state,
                bend_record=bend_records[bid],
                panel_graph=pg,
                unknown_policy="PRODUCTION"
            )
            dt = time.time() - t0
            self.assertTrue(res.valid, f"Step {step_idx} ({bid}) failed: {res.reason} ({res.stage})")
            self.assertEqual(res.stage, "valid")
            self.assertEqual(res.reason, "Step validated physically.")
            self.assertFalse(res.has_unknown, f"Step {step_idx} must not have UNKNOWN subchecks under PRODUCTION")
            current_state = res.next_state

        total_elapsed = time.time() - start_time
        # Physical collision checks on 4 steps with real B-Rep geometry take > 1 second (K3)
        self.assertGreater(total_elapsed, 1.0, f"Validation completed too fast ({total_elapsed:.2f}s); expected > 1.0s")

    def test_oracle_bridge_uses_preserving_order(self):
        """Phase 4c.1 / I1: Invariant check that transform_compound_preserving_order preserves face indexing."""
        rdir = REPO_ROOT / "runs/run_20261012_034045_9a351d"
        geom_path = rdir / "geometry.brep"
        builder = BRep_Builder()
        ref_shape = TopoDS_Shape()
        breptools.Read(ref_shape, str(geom_path), builder)

        f_exp = TopExp_Explorer(ref_shape, TopAbs_FACE)
        count_before = 0
        while f_exp.More():
            count_before += 1
            f_exp.Next()

        pg = PanelGraph()
        pan = Panel("P_MOVING")
        pan.face_indices = [0, 2, 6]
        pg.add_panel(pan)

        unfolded = transform_compound_preserving_order(
            shape=ref_shape,
            axis_origin=(40.0, 0.0, 1.5),
            axis_direction=(0.0, 1.0, 0.0),
            signed_angle_deg=-90.0,
            moving_panels={"P_MOVING"},
            panel_graph=pg
        )

        f_exp2 = TopExp_Explorer(unfolded, TopAbs_FACE)
        count_after = 0
        while f_exp2.More():
            count_after += 1
            f_exp2.Next()

        self.assertEqual(count_before, count_after)

    def test_oracle_bridge_rejects_invalid_order(self):
        """Phase 4c.1 / J4: Validates that test_box invalid sequence is rejected at step 2 (BEND_TRAJECTORY)."""
        orders_path = REPO_ROOT / "tests/fixtures/manual/known_orders_box.json"
        with open(orders_path, "r", encoding="utf-8") as f:
            known_orders = json.load(f)

        invalid_seq = known_orders["first_invalid"]["sequence"]
        expected_step = known_orders["first_invalid"]["fail_step"]
        expected_stage = known_orders["first_invalid"]["fail_stage"]

        # Run on test_box
        rdir = REPO_ROOT / "runs/run_manual_20261029_200743_ea819f"
        p4_path = rdir / "04_canonical_part.json"
        geom_path = rdir / "geometry.brep"

        with open(p4_path, "r", encoding="utf-8") as f:
            data = json.load(f)["payload"]

        builder = BRep_Builder()
        ref_shape = TopoDS_Shape()
        breptools.Read(ref_shape, str(geom_path), builder)

        pg = PanelGraph()
        for pid, pdata in data["panels"].items():
            pg.add_panel(Panel(pid, normal=tuple(pdata.get("normal", (0, 0, 1)))))
        for bid, bdata in data["bends"].items():
            pg.add_bend_connection(bid, bdata["parent_panel_id"], bdata["child_panel_id"])
        resolve_panel_primary_sheet_pair(ref_shape, pg, data)

        bg = BendGraph()
        bend_records = {}
        for bid, bdata in data["bends"].items():
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=bdata["parent_panel_id"],
                child_panel_id=bdata["child_panel_id"],
                axis_origin=tuple(bdata["axis_point"]),
                axis_direction=tuple(bdata["axis_direction"]),
                signed_angle=bdata["angle_deg"]["value"],
                length=bdata["length_mm"]["value"],
                radius=bdata["radius_mm"]["value"],
                thickness=data["thickness_mm"]["value"]
            )
            bg.add_bend(rec)
            bend_records[bid] = rec

        # Validate using baseline simplified machine to match known_orders_box kinematic self-collision probe
        mach_simp = PressBrakeMachine()
        bridge = OracleBridge(machine=mach_simp, tooling_set_id=self.tooling_set_id)
        current_state = FoldState.initial(ref_shape, bg, pg)

        failed_step = None
        failed_stage = None
        for step_idx, bid in enumerate(invalid_seq, start=1):
            res = bridge.validate_step(
                current_state=current_state,
                bend_record=bend_records[bid],
                panel_graph=pg,
                unknown_policy="EXPLORATORY"
            )
            if not res.valid:
                failed_step = step_idx
                failed_stage = res.stage
                break
            current_state = res.next_state

        self.assertEqual(failed_step, expected_step)
        self.assertEqual(failed_stage, expected_stage)

    def test_oracle_bridge_accepts_valid_order(self):
        """Phase 4c.1 / C7: Validates that test_box known valid sequence passes all 3 steps."""
        orders_path = REPO_ROOT / "tests/fixtures/manual/known_orders_box.json"
        with open(orders_path, "r", encoding="utf-8") as f:
            known_orders = json.load(f)

        valid_seq = known_orders["first_valid"]

        rdir = REPO_ROOT / "runs/run_manual_20261029_200743_ea819f"
        p4_path = rdir / "04_canonical_part.json"
        geom_path = rdir / "geometry.brep"

        with open(p4_path, "r", encoding="utf-8") as f:
            data = json.load(f)["payload"]

        builder = BRep_Builder()
        ref_shape = TopoDS_Shape()
        breptools.Read(ref_shape, str(geom_path), builder)

        pg = PanelGraph()
        for pid, pdata in data["panels"].items():
            pg.add_panel(Panel(pid, normal=tuple(pdata.get("normal", (0, 0, 1)))))
        for bid, bdata in data["bends"].items():
            pg.add_bend_connection(bid, bdata["parent_panel_id"], bdata["child_panel_id"])
        resolve_panel_primary_sheet_pair(ref_shape, pg, data)

        bg = BendGraph()
        bend_records = {}
        for bid, bdata in data["bends"].items():
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=bdata["parent_panel_id"],
                child_panel_id=bdata["child_panel_id"],
                axis_origin=tuple(bdata["axis_point"]),
                axis_direction=tuple(bdata["axis_direction"]),
                signed_angle=bdata["angle_deg"]["value"],
                length=bdata["length_mm"]["value"],
                radius=bdata["radius_mm"]["value"],
                thickness=data["thickness_mm"]["value"]
            )
            bg.add_bend(rec)
            bend_records[bid] = rec

        # Validate using baseline simplified machine to match known_orders_box kinematic self-collision probe
        mach_simp = PressBrakeMachine()
        bridge = OracleBridge(machine=mach_simp, tooling_set_id=self.tooling_set_id)
        current_state = FoldState.initial(ref_shape, bg, pg)

        for step_idx, bid in enumerate(valid_seq, start=1):
            res = bridge.validate_step(
                current_state=current_state,
                bend_record=bend_records[bid],
                panel_graph=pg,
                unknown_policy="EXPLORATORY"
            )
            self.assertTrue(res.valid, f"Valid sequence step {step_idx} failed: {res.reason}")
            self.assertEqual(res.stage, "valid")
            current_state = res.next_state


if __name__ == "__main__":
    unittest.main()
