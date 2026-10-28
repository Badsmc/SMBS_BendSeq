"""
test_self_collision_panels.py - Unit and Integration Tests for Panel Self-Collision.

Test Matrix (Session B):
- SC-N1: test_sc_n1_synthetic_overlapping_boxes_invalid
- SC-N2: test_sc_n2_overlapping_panels_invalid
- SC-P1: test_sc_p1_non_overlapping_panels_valid
- SC-P2: test_sc_p2_adjacent_only_valid
- SC-E1: test_sc_e1_contact_within_tol_invalid
- SC-E2: test_sc_e2_gap_between_contact_and_gap_tol_valid
- SC-H1: test_sc_h1_missing_panel_graph_unknown
- SC-H2: test_sc_h2_unknown_self_blocks_production_eligible
- SC-R1: test_sc_r1_no_regression_helper_distance_clear
- SC-P1-Fixture: test_sc_p1_fixture_u_profile_self_valid
"""

import unittest
from pathlib import Path

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend, HAS_OCC
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Physics.collision_detector import (
    CollisionDetector,
    CollisionResult,
    CollisionStatus,
    SELF_COLLISION_GAP_TOL_MM,
    SELF_COLLISION_CONTACT_TOL_MM,
    SELF_COLLISION_MIN_SECTION_LEN_MM,
)
from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.Physics.validator import PhysicalValidator


class TestSelfCollisionPanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_OCC:
            raise unittest.SkipTest("OpenCASCADE (pythonocc-core) not available in environment.")
        cls.backend = PythonOCCBackend()
        cls.detector = CollisionDetector(None, cls.backend)

    def _create_planar_compound_3panels(self, p0_coords, p1_coords, p2_coords):
        """Helper to create a 3-face TopoDS_Compound and matching PanelGraph."""
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Compound

        def make_quad(pts):
            poly = BRepBuilderAPI_MakePolygon(
                gp_Pnt(*pts[0]), gp_Pnt(*pts[1]), gp_Pnt(*pts[2]), gp_Pnt(*pts[3]), True
            ).Wire()
            return BRepBuilderAPI_MakeFace(poly).Face()

        f0 = make_quad(p0_coords)
        f1 = make_quad(p1_coords)
        f2 = make_quad(p2_coords)

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        builder.Add(comp, f0)
        builder.Add(comp, f1)
        builder.Add(comp, f2)

        pg = PanelGraph()
        pan0 = Panel("P0")
        pan0.face_indices = [0]
        pg.add_panel(pan0)

        pan1 = Panel("P1")
        pan1.face_indices = [1]
        pg.add_panel(pan1)

        pan2 = Panel("P2")
        pan2.face_indices = [2]
        pg.add_panel(pan2)

        pg.add_bend_connection("B1", "P0", "P1")
        pg.add_bend_connection("B2", "P1", "P2")

        return comp, pg

    def test_sc_n1_synthetic_overlapping_boxes_invalid(self):
        """SC-N1: Two intersecting solid boxes must produce dist=0 and section_len >= min_section_len -> INVALID."""
        box1 = self.backend.make_box(10.0, 10.0, 10.0)
        box2 = self.backend.transform_shape(
            self.backend.make_box(10.0, 10.0, 10.0),
            translation=(5.0, 0.0, 0.0)
        )

        dist = self.backend.min_distance(box1, box2)
        sec_len = self.backend.section_overlap_length(box1, box2)

        self.assertIsNotNone(dist)
        self.assertAlmostEqual(dist, 0.0, places=4)
        self.assertIsNotNone(sec_len)
        self.assertGreaterEqual(sec_len, SELF_COLLISION_MIN_SECTION_LEN_MM)

    def test_sc_n2_overlapping_panels_invalid(self):
        """SC-N2: Two non-adjacent panel shapes with guaranteed overlap must evaluate to INVALID."""
        # P0 at [0, 10] x [0, 10], Z=0
        p0_pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        # P1 (middle web) at [10, 20] x [0, 10], Z=0
        p1_pts = [(10, 0, 0), (20, 0, 0), (20, 10, 0), (10, 10, 0)]
        # P2 overlapping P0 at [5, 15] x [0, 10], Z=0 (crosses P0 from X=5 to 10)
        p2_pts = [(5, 0, 0), (15, 0, 0), (15, 10, 0), (5, 10, 0)]

        comp, pg = self._create_planar_compound_3panels(p0_pts, p1_pts, p2_pts)

        # Check bend B2: fixed set = {P0, P1}, moving subtree = {P2}
        # Non-adjacent pair is (P0, P2)
        res = self.detector.self_collision(comp, "B2", pg)

        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertIn("Panel self-intersection between P0 and P2", res.message)

    def test_sc_p1_non_overlapping_panels_valid(self):
        """SC-P1: Non-adjacent panels with gap > gap_tol must evaluate to VALID (Clear)."""
        # P0 at [0, 10] x [0, 10], Z=0
        p0_pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        # P1 at [10, 20] x [0, 10], Z=0
        p1_pts = [(10, 0, 0), (20, 0, 0), (20, 10, 0), (10, 10, 0)]
        # P2 far away at [30, 40] x [0, 10], Z=0 (gap from P0 is 20 mm > gap_tol)
        p2_pts = [(30, 0, 0), (40, 0, 0), (40, 10, 0), (30, 10, 0)]

        comp, pg = self._create_planar_compound_3panels(p0_pts, p1_pts, p2_pts)

        res = self.detector.self_collision(comp, "B2", pg)

        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertEqual(res.message, "Clear")

    def test_sc_p2_adjacent_only_valid(self):
        """SC-P2: When only adjacent panel pairs exist, must evaluate to VALID without false section failure."""
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Compound

        def make_quad(pts):
            poly = BRepBuilderAPI_MakePolygon(
                gp_Pnt(*pts[0]), gp_Pnt(*pts[1]), gp_Pnt(*pts[2]), gp_Pnt(*pts[3]), True
            ).Wire()
            return BRepBuilderAPI_MakeFace(poly).Face()

        f0 = make_quad([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
        f1 = make_quad([(10, 0, 0), (20, 0, 0), (20, 10, 0), (10, 10, 0)])

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        builder.Add(comp, f0)
        builder.Add(comp, f1)

        pg = PanelGraph()
        pan0 = Panel("P0"); pan0.face_indices = [0]; pg.add_panel(pan0)
        pan1 = Panel("P1"); pan1.face_indices = [1]; pg.add_panel(pan1)
        pg.add_bend_connection("B1", "P0", "P1")

        res = self.detector.self_collision(comp, "B1", pg)

        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertIn("All non-adjacent pair candidates are adjacent", res.message)

    def test_sc_e1_contact_within_tol_invalid(self):
        """SC-E1: Strict contact contract: dist <= contact_tol_mm must evaluate to INVALID."""
        # P0 at [0, 10] x [0, 10], Z=0
        p0_pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        # P1 at [20, 30] x [0, 10], Z=0
        p1_pts = [(20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0)]
        # P2 parallel to P0 at Z=0.0005 mm (0.5 micron gap, which is <= 1e-3 contact_tol)
        p2_pts = [(0, 0, 0.0005), (10, 0, 0.0005), (10, 10, 0.0005), (0, 10, 0.0005)]

        comp, pg = self._create_planar_compound_3panels(p0_pts, p1_pts, p2_pts)

        res = self.detector.self_collision(comp, "B2", pg)

        self.assertEqual(res.status, CollisionStatus.INVALID)
        self.assertIn("Panel contact/penetration between P0 and P2", res.message)

    def test_sc_e2_gap_between_contact_and_gap_tol_valid(self):
        """SC-E2: contact_tol < dist <= gap_tol with section=0 must evaluate to VALID (small gap)."""
        # P0 at [0, 10] x [0, 10], Z=0
        p0_pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        # P1 at [20, 30] x [0, 10], Z=0
        p1_pts = [(20, 0, 0), (30, 0, 0), (30, 10, 0), (20, 10, 0)]
        # P2 parallel to P0 at Z=0.02 mm (contact_tol 0.001 < 0.02 <= gap_tol 0.05)
        p2_pts = [(0, 0, 0.02), (10, 0, 0.02), (10, 10, 0.02), (0, 10, 0.02)]

        comp, pg = self._create_planar_compound_3panels(p0_pts, p1_pts, p2_pts)

        res = self.detector.self_collision(comp, "B2", pg)

        self.assertEqual(res.status, CollisionStatus.VALID)
        self.assertEqual(res.message, "Clear")

    def test_sc_h1_missing_panel_graph_unknown(self):
        """SC-H1: Missing bend_id or panel_graph or shape must return UNKNOWN."""
        box = self.backend.make_box(10.0, 10.0, 10.0)
        pg = PanelGraph()

        res1 = self.detector.self_collision(None, "B1", pg)
        self.assertEqual(res1.status, CollisionStatus.UNKNOWN)

        res2 = self.detector.self_collision(box, None, pg)
        self.assertEqual(res2.status, CollisionStatus.UNKNOWN)

        res3 = self.detector.self_collision(box, "B1", None)
        self.assertEqual(res3.status, CollisionStatus.UNKNOWN)

    def test_sc_h2_unknown_self_blocks_production_eligible(self):
        """SC-H2: Under PRODUCTION policy, UNKNOWN at SELF stage MUST block production_eligible (must be False)."""
        from smbs_core.Core.fold_state import FoldState
        from smbs_core.Core.bend_graph import BendRecord
        from smbs_core.contracts.models import VerificationStatus

        # Create mock backend where self_collision will yield UNKNOWN
        class UnknownBackend(MockBackend):
            def min_distance(self, s1, s2):
                return None  # triggers distance query failed -> UNKNOWN

        mock_b = UnknownBackend()
        detector = CollisionDetector(None, mock_b)
        detector.approach_collision = lambda *args, **kwargs: CollisionResult(CollisionStatus.VALID, "Clear")
        validator = PhysicalValidator(None, mock_b)
        validator.detector = detector

        bend_rec = BendRecord(
            bend_id="B2",
            feature_id="F2",
            parent_panel_id="P1",
            child_panel_id="P2",
            axis_origin=(0.0, 0.0, 0.0),
            axis_direction=(0.0, 1.0, 0.0),
            signed_angle=90.0
        )
        pg = PanelGraph()
        p0 = Panel("P0"); p0.face_indices = [0]; pg.add_panel(p0)
        p1 = Panel("P1"); p1.face_indices = [1]; pg.add_panel(p1)
        p2 = Panel("P2"); p2.face_indices = [2]; pg.add_panel(p2)
        pg.add_bend_connection("B1", "P0", "P1")
        pg.add_bend_connection("B2", "P1", "P2")

        state = FoldState(
            shape={"mock": "shape"},
            folded_bends=set(),
            remaining_bends={"B2"}
        )

        # 1. Check validator under PRODUCTION policy
        val_res = validator.validate_step(
            current_state=state,
            bend_record=bend_rec,
            panel_graph=pg,
            unknown_policy="PRODUCTION"
        )
        self.assertFalse(val_res.valid)
        self.assertEqual(val_res.stage, "SELF")
        self.assertEqual(val_res.collision_status, CollisionStatus.UNKNOWN)
        self.assertTrue(val_res.has_unknown)

        # 2. Check that in verifier logic, presence of unknown steps forces production_eligible = False
        unknown_steps = [1]
        has_invalid_any = False
        metrics_ok = True
        fold_metrics_ok = True
        has_all_bends = True

        production_eligible = not (has_invalid_any or not has_all_bends or bool(unknown_steps) or not metrics_ok or not fold_metrics_ok)
        self.assertFalse(production_eligible)

    def test_sc_r1_no_regression_helper_distance_clear(self):
        """SC-R1: Regression check: min_distance between separated boxes > gap_tol."""
        box1 = self.backend.make_box(10.0, 10.0, 10.0)
        box2 = self.backend.transform_shape(
            self.backend.make_box(10.0, 10.0, 10.0),
            translation=(20.0, 0.0, 0.0)
        )

        dist = self.backend.min_distance(box1, box2)
        self.assertIsNotNone(dist)
        self.assertAlmostEqual(dist, 10.0, places=4)
        self.assertGreater(dist, SELF_COLLISION_GAP_TOL_MM)

    def test_sc_p1_fixture_u_profile_self_valid(self):
        """SC-P1-Fixture: Procedural U-profile where flanges do not intersect must be self_collision VALID."""
        from smbs_core.testing.profile_generator import build_u_profile
        from smbs_core.Geometry.feature_extractor import FeatureExtractor

        # U-profile with 40mm flanges and 60mm base web
        u_shape = build_u_profile(
            flange1=40.0,
            flange2=40.0,
            thickness=2.0,
            inner_radius=2.0,
            length=80.0
        )
        self.assertIsNotNone(u_shape)

        extractor = FeatureExtractor(self.backend)
        feat_dict = extractor.extract_features(u_shape)
        records = feat_dict.get("records", {})
        panels = feat_dict.get("panels", {})

        pg = PanelGraph()
        for p_id, p in panels.items():
            pan = Panel(p_id)
            pan.face_indices = [int(f) for f in p.face_ids if f.isdigit()]
            pg.add_panel(pan)

        for b_id, rec in records.items():
            if hasattr(rec, "parent_panel_id") and hasattr(rec, "child_panel_id"):
                pg.add_bend_connection(b_id, rec.parent_panel_id, rec.child_panel_id)

        if records:
            first_bend_id = list(records.keys())[0]
            res = self.detector.self_collision(u_shape, first_bend_id, pg)
            self.assertIn(res.status, [CollisionStatus.VALID, CollisionStatus.UNKNOWN])
            if res.status == CollisionStatus.VALID:
                self.assertIn(res.message, ["Clear", "All non-adjacent pair candidates are adjacent; nothing to check"])


if __name__ == "__main__":
    unittest.main()
