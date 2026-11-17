"""Pure-math rigid-pose reuse tests for Coin3D sheet geometry."""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.preview_mesh_3d import (
    PreviewFace3D, PreviewMeshError, IDENTITY_PLACEMENT,
    transform_preview_faces,
)


class TestCoinMeshPoseReuse(unittest.TestCase):
    def test_identity_machine_pose_preserves_preview_vertices_and_holes(self):
        face = PreviewFace3D(
            ((-2., 10., 1.), (0., 10., 1.), (0., 11., 1.)),
            "panel_top", "P01",
            (((-1., 10., 1.), (-0.5, 10., 1.), (-0.5, 10.5, 1.)),),
        )
        result = transform_preview_faces((face,), IDENTITY_PLACEMENT)
        self.assertEqual(result, (face,))

    def test_machine_translation_uses_preview_axis_contract(self):
        face = PreviewFace3D(
            ((-2., 10., 1.), (0., 10., 1.), (0., 11., 1.)),
            "panel_top", "P01",
        )
        class Placement:
            def apply(self, xyz):
                return (xyz[0] + 3.0, xyz[1] - 4.0, xyz[2] + 5.0)
        moved, = transform_preview_faces((face,), Placement())
        self.assertEqual(moved.vertices[0], (2., 13., 6.))
        self.assertEqual(moved.vertices[1], (4., 13., 6.))

    def test_rotation_and_translation_combined(self):
        face = PreviewFace3D(
            ((-2., 10., 1.), (0., 10., 1.), (0., 11., 1.)),
            "panel_top", "P01",
        )
        class Placement:
            def apply(self, xyz):
                x, y, z = xyz
                return (-y + 7., x - 5., z + 2.)
        moved, = transform_preview_faces((face,), Placement())
        self.assertEqual(moved.vertices[0], (-5., 5., 3.))

    def test_invalid_placement_rejected(self):
        face = PreviewFace3D(
            ((0., 0., 0.), (1., 0., 0.), (1., 1., 0.)),
            "panel_top", "P",
        )
        with self.assertRaises(PreviewMeshError):
            transform_preview_faces((face,), None)


if __name__ == "__main__":
    unittest.main()
