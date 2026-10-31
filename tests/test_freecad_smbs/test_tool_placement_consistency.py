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
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
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
        self.assertIn("vol=", steps[1]["reason"])

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

    def test_open_surface_intersection_ambiguity_handling(self):
        """
        Requirement 2 & 7: Ambiguous open surface intersections with no solids produce UNKNOWN.
        Solid-solid, open-shell-solid, and tangent contact are properly audited.
        """
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.gp import gp_Trsf, gp_Vec
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.TopoDS import topods

        backend = PythonOCCBackend()
        box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()

        # Extract two faces
        exp = TopExp_Explorer(box, TopAbs_FACE)
        f1 = topods.Face(exp.Current())

        # Test 1: Tangential face on solid boundary -> VALID (Clear)
        res_tangent = backend.intersect_volume(f1, box)
        self.assertFalse(res_tangent.collides)
        self.assertEqual(res_tangent.status, "valid")

        # Test 2: Internal penetrating face inside solid -> INVALID (Collision)
        tr_in = gp_Trsf()
        tr_in.SetTranslation(gp_Vec(5.0, 0.0, 0.0))
        f_internal = BRepBuilderAPI_Transform(f1, tr_in, False).Shape()
        res_internal = backend.intersect_volume(f_internal, box)
        self.assertTrue(res_internal.collides)
        self.assertEqual(res_internal.status, "invalid")

        # Test 3: Ambiguous face-face intersection without solids -> UNKNOWN
        tr_overlap = gp_Trsf()
        tr_overlap.SetTranslation(gp_Vec(0.0, 2.0, 0.0))
        f_overlap = BRepBuilderAPI_Transform(f1, tr_overlap, False).Shape()
        res_face_face = backend.intersect_volume(f1, f_overlap)
        # Face-to-face open intersection without enclosing solids cannot determine solid penetration
        self.assertEqual(res_face_face.status, "unknown")
        self.assertIn("Ambiguous open-surface intersection", res_face_face.message)

    def test_part_punch_collision_and_tangential_contact(self):
        """
        Requirement 7: Add a negative test for part/punch collision and a positive test for actual tangential contact.
        Verifies that actual volumetric penetration into punch geometry yields INVALID,
        while genuine tangential contact along the tooling boundary yields VALID (Clear).
        """
        from smbs_core.Physics.machine_kinematics import PressBrakeMachine
        from smbs_core.Physics.collision_detector import CollisionDetector
        from smbs_core.Physics.tooling_assembly import ToolingKind
        from tests.test_technological_unfold_single_bend import create_l_bracket_shape
        from OCC.Core.gp import gp_Trsf, gp_Vec
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

        backend = PythonOCCBackend()
        punch_path = str(REPO_ROOT / "toollibrary/punch/puancon.FCStd")
        die_path = str(REPO_ROOT / "toollibrary/die/matrica.FCStd")

        machine = PressBrakeMachine(
            punch_path=punch_path, die_path=die_path,
            punch_target_object_name="Body", die_target_object_name="Body"
        )
        detector = CollisionDetector(machine, backend)

        # Baseline: L-bracket cleanly positioned at tool station
        l_shape = create_l_bracket_shape(
            angle_deg=90.0, radius=1.0, thickness=1.5,
            length=50.0, l1=95.5, l2=47.5
        )
        bend_info = {"origin": (95.5, 0.0, 2.5), "axis": (0.0, 1.0, 0.0), "angle": 90.0}
        placed = detector.place_part_on_tool_station(l_shape, bend_info)
        self.assertIsNotNone(placed, "Placement must succeed for valid L-bracket")

        punch_world = detector.assembly.get_world_shape(ToolingKind.PUNCH)
        self.assertIsNotNone(punch_world, "Punch shape must exist")

        # Positive test: Actual tangential contact / near-contact at tool station
        # In the valid placed orientation, the punch tip contacts the inner bend radius tangentially.
        res_tangent = detector._check_intersection(placed, punch_world, "Part", "Punch")
        self.assertTrue(res_tangent.is_valid, f"Tangential contact must be VALID, got: {res_tangent.message}")
        self.assertEqual(res_tangent.status, CollisionStatus.VALID)

        # Negative test: Shift part directly into the punch body to force volumetric penetration
        trsf_penetrate = gp_Trsf()
        trsf_penetrate.SetTranslation(gp_Vec(0.0, -25.0, 10.0))  # Into punch body
        colliding_part = BRepBuilderAPI_Transform(placed, trsf_penetrate, False).Shape()

        res_coll = detector._check_intersection(colliding_part, punch_world, "Part", "Punch")
        self.assertFalse(res_coll.is_valid, "Part deeply penetrating punch must NOT be valid")
        self.assertEqual(res_coll.status, CollisionStatus.INVALID)
        self.assertIn("Intersection detected", res_coll.message)


if __name__ == "__main__":
    unittest.main()

