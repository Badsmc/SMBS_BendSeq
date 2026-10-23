"""
KL-22 regression: rotate_subtree must preserve face identity across depth >= 2
unfold operations on BRep fixtures with unassigned side edges.

Before KL-22 fix (ed366e7): box_100out_50in_ok A* search returned FAILED with
~40 UNFOLD_TRANSFORM rejections at depth 2 (face count collapsed 78 -> 41
because unassigned side edges fell out of BRepBuilderAPI_Sewing; panel
face_indices became stale, extract_faces returned null, rotate_subtree raised
InvalidRotationError).

After KL-22 fix: unassigned faces are attached to fixed side before sewing.
Face identity is preserved (78 -> 78 at every depth). Search reaches
multi-depth nodes; semantic side-edge kinematics remains open as KL-12 v2
(P2, before Req 2). Results on such fixtures stay production_eligible=False.

Test scope is intentionally UNIT-LEVEL, not e2e search: full A* on box
takes ~36 minutes (2149s pre-Lazy-A*, Req 6a scope). Regression covers
the specific face-identity invariant, not end-to-end search time.
"""
import unittest
from pathlib import Path

from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.bend_transform import BendTransform
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures/freecad/box_100out_50in_ok/part_3d.brep"


def _setup_box():
    """Load box fixture, extract features, build BendGraph + PanelGraph with
    resolved panel face_indices. Returns (shape, backend, feats, bg, pg)."""
    shape = TopoDS_Shape()
    breptools.Read(shape, str(FIXTURE), BRep_Builder())
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
    resolve_panel_faces(shape, pg, {
        "thickness_mm": {"value": feats["thickness"]},
        "panels": {
            pid: {"normal": pd["normal"], "centroid": pd["centroid"]}
            for pid, pd in feats["panels"].items()
        },
        "bends": {
            bid: {"axis_point": bi["origin"],
                  "child_panel_id": bi["child_panel_id"]}
            for bid, bi in feats["bends"].items()
        },
    })
    return shape, backend, feats, bg, pg


class TestKL22FacePreservation(unittest.TestCase):
    """KL-22: rotate_subtree must preserve unassigned faces across sew."""

    def test_depth1_preserves_face_count(self):
        """Single unfold on initial box must keep face count (78 -> 78).
        Pre-KL-22 this collapsed to 41 because 37 unassigned side edges were
        dropped by BRepBuilderAPI_Sewing."""
        shape, backend, feats, bg, pg = _setup_box()
        initial = len(backend.get_faces(shape))
        self.assertEqual(initial, 78,
                         "fixture precondition: box_100out_50in_ok expected 78 faces")

        bid = next(iter(feats["bends"]))
        b = bg.get_bend(bid)
        s1 = BendTransform.rotate_subtree(
            backend, shape, b.axis_origin, b.axis_direction,
            -b.signed_angle, pg.moving_subtree(bid), pg,
        )
        self.assertFalse(s1.IsNull(), "depth 1 unfold must produce non-null shape")
        self.assertEqual(
            len(backend.get_faces(s1)), initial,
            "KL-22: unassigned side edges must be preserved (78 -> 78)",
        )

    def test_depth2_unfold_does_not_raise(self):
        """Second unfold on intermediate shape must not raise InvalidRotationError.
        Pre-KL-22 this exact step raised 'moving_shape is None or IsNull'
        because panel.face_indices referenced indices beyond the 41-face
        intermediate shape."""
        shape, backend, feats, bg, pg = _setup_box()

        bids = list(feats["bends"].keys())
        b1, b2 = bids[0], bids[1]

        bend1 = bg.get_bend(b1)
        s1 = BendTransform.rotate_subtree(
            backend, shape, bend1.axis_origin, bend1.axis_direction,
            -bend1.signed_angle, pg.moving_subtree(b1), pg,
        )
        self.assertFalse(s1.IsNull(), "depth 1 must succeed")

        bend2 = bg.get_bend(b2)
        s2 = BendTransform.rotate_subtree(
            backend, s1, bend2.axis_origin, bend2.axis_direction,
            -bend2.signed_angle, pg.moving_subtree(b2), pg,
        )
        self.assertFalse(s2.IsNull(), "depth 2 unfold must succeed (KL-22)")

    def test_depth2_preserves_face_count(self):
        """Face count must stay stable across two successive unfolds.
        KL-22 fix guarantees identity is preserved regardless of depth."""
        shape, backend, feats, bg, pg = _setup_box()
        initial = len(backend.get_faces(shape))

        bids = list(feats["bends"].keys())
        current = shape
        for bid in bids[:2]:
            bend = bg.get_bend(bid)
            current = BendTransform.rotate_subtree(
                backend, current, bend.axis_origin, bend.axis_direction,
                -bend.signed_angle, pg.moving_subtree(bid), pg,
            )
            self.assertFalse(current.IsNull())
            self.assertEqual(
                len(backend.get_faces(current)), initial,
                f"KL-22: face count must remain {initial} after unfolding {bid}",
            )


if __name__ == "__main__":
    unittest.main()
