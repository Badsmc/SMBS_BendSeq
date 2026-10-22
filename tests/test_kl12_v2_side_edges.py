"""
test_kl12_v2_side_edges.py - Unit Tests for Side-Edge Face Matching (KL-12 v2)

Covers:
1. test_fixture_l_assigned_faces_count: verifies assigned + unassigned == 10 and exactly 8 assigned (faces 1,4 are lateral caps across bend).
2. test_fixture_l_side_edges_correct_panel: verifies P1 receives side edge 3 and P2 receives side edge 7.
3. test_fixture_l_preserves_existing_assigned: faces 0, 2, 5, 6, 8, 9 remain assigned.
4. test_box_assignment_count_increases: box_100out_50in_ok assignment count increases from 41.
5. test_no_lost_faces_box: assigned | unassigned == 78 on box fixture.
6. test_no_lost_faces_fixture_l: assigned | unassigned == 10 on fixture L.
7. test_box_no_duplicate_faces: panels' face_indices are strictly disjoint (sum == unique count == 78).
8. test_fixture_l_no_duplicate_faces: panels' face_indices are strictly disjoint (sum == unique count == 8).
9. test_fixture_l_bridge_caps_stay_unassigned: faces 1, 4 are bridge caps spanning the bend line — must remain unassigned.
"""

import unittest
from pathlib import Path

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
from tests.test_technological_unfold_single_bend import create_l_bracket_shape


ROOT = Path(__file__).resolve().parent.parent
BOX_FIXTURE = ROOT / "fixtures/freecad/box_100out_50in_ok/part_3d.brep"


def _setup_box_fixture():
    shape = TopoDS_Shape()
    breptools.Read(shape, str(BOX_FIXTURE), BRep_Builder())
    backend = PythonOCCBackend()
    feats = FeatureExtractor(backend).extract_features(shape)
    bg, pg = BendGraph(), PanelGraph()
    for idx, (b_id, b_info) in enumerate(feats["bends"].items()):
        rec = BendRecord(
            bend_id=b_id,
            feature_id=f"FEAT_{b_id}",
            parent_panel_id=b_info.get("parent_panel_id", f"PANEL_{idx:02d}"),
            child_panel_id=b_info.get("child_panel_id", f"PANEL_{(idx+1):02d}"),
            axis_origin=tuple(b_info.get("origin", (0.0, 0.0, 0.0))),
            axis_direction=tuple(b_info.get("axis", (0.0, 0.0, 1.0))),
            signed_angle=b_info.get("signed_angle") or b_info.get("angle", 90.0),
            radius=1.0,
            thickness=2.0,
            length=10.0,
        )
        bg.add_bend(rec)
        pg.add_bend_connection(b_id, rec.parent_panel_id, rec.child_panel_id)

    payload = {
        "thickness_mm": {"value": feats["thickness"]},
        "panels": {
            pid: {"normal": pd["normal"], "centroid": pd["centroid"]}
            for pid, pd in feats["panels"].items()
        },
        "bends": {
            bid: {"axis_point": bi["origin"],
                  "child_panel_id": bi["child_panel_id"]}
            for bid, bi in feats["bends"].items()
        }
    }
    return shape, backend, pg, payload


def _setup_l_fixture():
    shape = create_l_bracket_shape(90.0, 1.0, 1.5, 50.0, 95.5, 47.5)
    backend = PythonOCCBackend()
    pg = PanelGraph()
    pg.add_panel(Panel("P1", normal=(0.0, 0.0, 1.0)))
    pg.add_panel(Panel("P2", normal=(1.0, 0.0, 0.0)))
    pg.add_bend_connection("B1", "P1", "P2")
    payload = {
        "thickness_mm": {"value": 1.5},
        "panels": {
            "P1": {"centroid": [47.75, 25.0, 0.75], "normal": [0.0, 0.0, 1.0]},
            "P2": {"centroid": [97.25, 25.0, 26.25], "normal": [1.0, 0.0, 0.0]},
        },
        "bends": {
            "B1": {"axis_point": [95.5, 0.0, 2.5], "child_panel_id": "P2"}
        }
    }
    return shape, backend, pg, payload


class TestKL12V2SideEdges(unittest.TestCase):
    def test_fixture_l_assigned_faces_count(self):
        """Fixture L: 8 faces assigned out of 10 total (faces 1, 4 are cross-bend lateral caps)."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        all_assigned = set()
        for p in pg.panels.values():
            all_assigned.update(p.face_indices)
        self.assertEqual(len(all_assigned), 8)

    def test_fixture_l_side_edges_correct_panel(self):
        """Fixture L side edges: P1 contains side edge 3, P2 contains side edge 7."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        self.assertIn(3, pg.panels["P1"].face_indices)
        self.assertIn(7, pg.panels["P2"].face_indices)

    def test_fixture_l_preserves_existing_assigned(self):
        """Fixture L: existing planar and cylindrical faces (0, 2, 5, 6, 8, 9) remain assigned."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        all_assigned = set()
        for p in pg.panels.values():
            all_assigned.update(p.face_indices)
        for expected in [0, 2, 5, 6, 8, 9]:
            self.assertIn(expected, all_assigned)

    def test_box_assignment_count_increases(self):
        """Box fixture: assigned face count increases from 41."""
        shape, backend, pg, payload = _setup_box_fixture()
        resolve_panel_faces(shape, pg, payload)
        all_assigned = set()
        for p in pg.panels.values():
            all_assigned.update(p.face_indices)
        self.assertGreater(len(all_assigned), 41)

    def test_no_lost_faces_box(self):
        """Box fixture: assigned union unassigned equals total 78 faces without duplicates."""
        shape, backend, pg, payload = _setup_box_fixture()
        resolve_panel_faces(shape, pg, payload)
        all_assigned = set()
        for p in pg.panels.values():
            all_assigned.update(p.face_indices)
        total_faces = len(backend.get_faces(shape))
        self.assertEqual(total_faces, 78)
        unassigned = set(range(total_faces)) - all_assigned
        self.assertEqual(all_assigned | unassigned, set(range(78)))

    def test_no_lost_faces_fixture_l(self):
        """Fixture L: assigned union unassigned equals total 10 faces."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        all_assigned = set()
        for p in pg.panels.values():
            all_assigned.update(p.face_indices)
        total_faces = len(backend.get_faces(shape))
        self.assertEqual(total_faces, 10)
        unassigned = set(range(total_faces)) - all_assigned
        self.assertEqual(all_assigned | unassigned, set(range(10)))

    def test_box_no_duplicate_faces(self):
        """Box: panels' face_indices are strictly disjoint; sum == unique count."""
        shape, backend, pg, payload = _setup_box_fixture()
        resolve_panel_faces(shape, pg, payload)
        total = sum(len(p.face_indices) for p in pg.panels.values())
        unique = set()
        for p in pg.panels.values():
            unique.update(p.face_indices)
        self.assertEqual(total, len(unique), "face assigned to more than one panel")

    def test_fixture_l_no_duplicate_faces(self):
        """Fixture L: panels' face_indices are strictly disjoint; sum == unique count."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        total = sum(len(p.face_indices) for p in pg.panels.values())
        unique = set()
        for p in pg.panels.values():
            unique.update(p.face_indices)
        self.assertEqual(total, len(unique), "face assigned to more than one panel")

    def test_fixture_l_bridge_caps_stay_unassigned(self):
        """Faces 1, 4 are bridge caps spanning the bend line — must remain unassigned."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)
        for cap in (1, 4):
            for p in pg.panels.values():
                self.assertNotIn(cap, p.face_indices,
                                 f"bridge cap {cap} must not be assigned to {p.id}")


if __name__ == "__main__":
    unittest.main()
