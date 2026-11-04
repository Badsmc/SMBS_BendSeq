"""Unit tests for full-sheet 3D surface preview driven only by FoldState."""
import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.preview_mesh_3d import (
    PreviewMeshError, build_sheet_preview_faces,
)
from smbs_core.kinematics2d.workpiece_pose_2d import WorkpiecePose2D


class Transform:
    def __init__(self, angle=0):
        self.angle = math.radians(angle)

    def point(self, xyz):
        x, y, z = xyz
        return (
            x*math.cos(self.angle)-z*math.sin(self.angle),
            y,
            x*math.sin(self.angle)+z*math.cos(self.angle),
        )


class FoldStateFixture:
    def __init__(self, angle=0):
        self.graph = SimpleNamespace(panels=("Left", "Right"))
        self.transforms = {"Left": Transform(), "Right": Transform(angle)}
        self.zone = SimpleNamespace(
            origin_xy=(0., 0.), axis_dir_xy=(0., 1.),
            left_xy=(-1., 0.), axis_xy=((0., 0.), (0., 60.)),
            thickness_mm=2.0,
        )
        self.bends = {
            "B01": SimpleNamespace(
                bend_id="B01", fixed_panel="Left", moving_left=False,
                bend_zone=self.zone,
            )
        }
        self.angle = angle

    def zone_section(self, bend_id, segments=12):
        return {
            "inner": ((-1., 0.), (0., 0.), (1., 0.)),
            "outer": ((-1., -2.), (0., -2.), (1., -2.)),
        }


class Preview3DTests(unittest.TestCase):
    def setUp(self):
        self.outlines = {
            "Left": ((-55., 0.), (-2., 0.), (-2., 60.), (-55., 60.)),
            "Right": ((2., 0.), (45., 0.), (45., 60.), (2., 60.)),
        }

    def test_both_panels_and_zone_are_present(self):
        state = FoldStateFixture()
        faces = build_sheet_preview_faces(
            state, self.outlines, "B01", WorkpiecePose2D(),
        )
        kinds = {f.kind for f in faces}
        self.assertIn("panel_top", kinds)
        self.assertIn("panel_bottom", kinds)
        self.assertIn("zone_inner", kinds)
        self.assertIn("zone_outer", kinds)
        self.assertEqual({f.identifier for f in faces if f.kind=="panel_top"},
                         {"Left", "Right"})
        self.assertTrue(all(len(f.vertices) >= 3 for f in faces))

    def test_animation_moves_right_panel_not_left_or_source(self):
        flat = FoldStateFixture(0)
        bent = FoldStateFixture(45)
        first = build_sheet_preview_faces(
            flat, self.outlines, "B01", WorkpiecePose2D()
        )
        second = build_sheet_preview_faces(
            bent, self.outlines, "B01", WorkpiecePose2D()
        )
        def vertices(mesh, name):
            return [f.vertices for f in mesh
                    if f.kind=="panel_top" and f.identifier==name][0]
        self.assertEqual(vertices(first,"Left"),vertices(second,"Left"))
        self.assertNotEqual(vertices(first,"Right"),vertices(second,"Right"))
        self.assertEqual(self.outlines["Right"][0], (2., 0.))

    def test_pose_transforms_all_depth_layers(self):
        state = FoldStateFixture(45)
        pose = WorkpiecePose2D(rotation_deg=12, shift=(25., -8.))
        transformed = build_sheet_preview_faces(state,self.outlines,"B01",pose)
        original = build_sheet_preview_faces(state,self.outlines,"B01",WorkpiecePose2D())
        for base, moved in zip(original, transformed):
            for p,q in zip(base.vertices,moved.vertices):
                s,z = pose.apply((p[0],p[2]))
                self.assertAlmostEqual(s,q[0],places=6)
                self.assertAlmostEqual(z,q[2],places=6)
                self.assertAlmostEqual(p[1],q[1],places=6)

    def test_invalid_active_bend_fails_closed(self):
        with self.assertRaises(PreviewMeshError):
            build_sheet_preview_faces(
                FoldStateFixture(),self.outlines,"UNKNOWN",WorkpiecePose2D()
            )


if __name__ == "__main__":
    unittest.main()
