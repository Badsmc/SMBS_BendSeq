"""Headless projection tests for interactive PressBrakeSim camera."""
import math
import unittest

from freecad_smbs.ui.orbit_camera import orbit_project, clamp_pitch


class OrbitCameraTests(unittest.TestCase):
    def test_default_projection_is_finite(self):
        self.assertTrue(all(math.isfinite(v) for v in orbit_project((10, 20, 30))))

    def test_zero_camera_has_orthographic_axes(self):
        self.assertEqual(orbit_project((3, 5, 7), 0, 0, 0), (3.0, -7.0, 5.0))

    def test_yaw_changes_horizontal_projection(self):
        before = orbit_project((20, 0, 0), 0, 0, 0)
        after = orbit_project((20, 0, 0), 90, 0, 0)
        self.assertAlmostEqual(after[0], 0.0, places=8)
        self.assertNotAlmostEqual(before[0], after[0])

    def test_pitch_rotates_vertical_projection(self):
        before = orbit_project((0, 10, 0), 0, 0, 0)
        after = orbit_project((0, 10, 0), 0, 45, 0)
        self.assertNotAlmostEqual(before[1], after[1])

    def test_roll_rotates_screen_plane(self):
        before = orbit_project((10, 0, 0), 0, 0, 0)
        after = orbit_project((10, 0, 0), 0, 0, 90)
        self.assertAlmostEqual(after[0], 0, places=8)
        self.assertNotAlmostEqual(before[1], after[1])

    def test_camera_preserves_distance_in_3d(self):
        p = (12.5, -8.0, 4.0)
        transformed = orbit_project(p, 115, -40, 37)
        self.assertAlmostEqual(sum(v * v for v in transformed),
                               sum(v * v for v in p), places=8)

    def test_pitch_is_clamped(self):
        self.assertEqual(clamp_pitch(500), 89)
        self.assertEqual(clamp_pitch(-500), -89)


if __name__ == "__main__":
    unittest.main()
