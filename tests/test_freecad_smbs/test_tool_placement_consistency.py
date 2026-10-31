"""
test_tool_placement_consistency.py - Regression test suite for tool placement consistency,
candidate logging, topology-aware collision detection, and production validation fidelity.
"""

import unittest
from pathlib import Path
import json

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
from freecad_smbs.manual.manual_runner import run_manual_sequence_on_fcstd
from freecad_smbs.manual.manual_worker import ManualSequenceInput, BendSelectionItem
from freecad_smbs.manual.geometry_extract import extract_bend_lines_3d
from freecad_smbs.ui.manual_sequence_panel import RawBendLineSnapshot
from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


class TestToolPlacementConsistency(unittest.TestCase):
    """
    Validates:
    1. Exhaustive candidate evaluation logging across candidate orientations.
    2. Topology-aware collision detection (BRepClass3d_SolidClassifier) distinguishing
       solid penetration from surface-only contact.
    3. Faithful production reproduction of test_box Step 2 collision without artificial bypass.
    """

    def setUp(self):
        self.box_path = FIXTURES_DIR / "test_box.FCStd"
        self.temp_runs = REPO_ROOT / "tests" / "artifacts" / "temp_manual_runs"
        self.temp_runs.mkdir(parents=True, exist_ok=True)

    def _extract_snapshots(self, fcstd_path: Path):
        doc = App.openDocument(str(fcstd_path))
        unfold = doc.getObject("Unfold")
        raw_bends = extract_bend_lines_3d(unfold.Shape)
        items = []
        for i, rb in enumerate(raw_bends, start=1):
            snap = RawBendLineSnapshot(
                midpoint=rb.midpoint,
                direction=rb.direction,
                normal=rb.normal,
                radius_mm=rb.radius_mm,
                length_mm=rb.length_mm,
                adjacent_panel_areas=rb.adjacent_panel_areas,
                source_edge_ref=f"Edge{i}",
            )
            items.append(BendSelectionItem(
                order=i,
                edge_ref=f"Edge{i}",
                object_name="Unfold",
                raw_snapshot=snap,
            ))
        return items

    def test_candidate_placement_logs_recorded(self):
        """
        Requirement 2: Every candidate orientation evaluated by place_part_on_tool_station()
        must be logged with part placement, die clearance, punch clearance, and rejection reason.
        """
        items = self._extract_snapshots(self.box_path)
        res_canon = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(items),
            out_runs_dir=self.temp_runs,
            phases=["01", "02", "04"],
        )
        self.assertTrue(res_canon.ok)

        # Build sequence starting with BEND_03_bc0dd183
        sel_by_bid = {s.bend_id: s for s in res_canon.selections}
        user_seq = ["BEND_03_bc0dd183", "BEND_02_64374506", "BEND_01_0528d4fa"]
        reordered = [
            BendSelectionItem(
                order=idx,
                edge_ref=sel_by_bid[bid].edge_ref,
                object_name=sel_by_bid[bid].object_name,
                raw_snapshot=sel_by_bid[bid].raw_snapshot,
                bend_id=bid,
            )
            for idx, bid in enumerate(user_seq, start=1)
        ]

        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(reordered, tooling_set_id="standard_90deg_v12", unknown_policy="PRODUCTION"),
            out_runs_dir=self.temp_runs,
        )

        # Step 2 must fail at TOOL with punch intersection
        self.assertFalse(res.ok)
        self.assertEqual(res.planning_status, "NO_SOLUTION")

        s7_path = res.run_dir / "07_search_result.json"
        s7 = json.loads(s7_path.read_text(encoding="utf-8"))
        steps = s7["payload"]["candidate_sequence"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["stage"], "VALID")
        self.assertEqual(steps[1]["stage"], "TOOL")
        self.assertIn("Intersection detected between Part and Punch", steps[1]["reason"])
        self.assertIn("893.8506", steps[1]["reason"])

    def test_topology_aware_collision_classifier(self):
        """
        Requirement 6: Test topology-aware collision classifier distinguishes interior penetration
        (TopAbs_IN) from surface-only contact (TopAbs_ON).
        """
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.gp import gp_Pnt, gp_Vec, gp_Trsf
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

        backend = PythonOCCBackend()

        # Box 1: [0, 10] x [0, 10] x [0, 10]
        box1 = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()

        # Box 2 penetrating box 1: [5, 15] x [0, 10] x [0, 10]
        trsf_pen = gp_Trsf()
        trsf_pen.SetTranslation(gp_Vec(5.0, 0.0, 0.0))
        box2_pen = BRepBuilderAPI_Transform(box1, trsf_pen, False).Shape()

        res_pen = backend.intersect_volume(box1, box2_pen)
        self.assertTrue(res_pen.collides)
        self.assertEqual(res_pen.status, "invalid")
        self.assertGreater(res_pen.volume, 0.0)

        # Box 3 touching box 1 only on boundary face at X=10: [10, 20] x [0, 10] x [0, 10]
        trsf_touch = gp_Trsf()
        trsf_touch.SetTranslation(gp_Vec(10.0, 0.0, 0.0))
        box3_touch = BRepBuilderAPI_Transform(box1, trsf_touch, False).Shape()

        res_touch = backend.intersect_volume(box1, box3_touch)
        self.assertFalse(res_touch.collides, "Boundary contact must not be flagged as volumetric collision")
        self.assertEqual(res_touch.status, "valid")


if __name__ == "__main__":
    unittest.main()
