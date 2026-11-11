"""Height-interval and local machine-section candidate regression tests."""
import unittest

from smbs_core.kinematics2d.backgauge_contact_height import (
    contact_height_candidates, rear_edge_at_face_height,
)


class BackgaugeContactHeightTests(unittest.TestCase):
    def test_tall_panel_center_outside_travel_still_has_reachable_edge(self):
        # Pad.Face6 relative Z span from actual CAD datum (approx -6.75..7.25).
        heights = contact_height_candidates(
            0.0, 300.0, -6.75308, 7.24692, -20.0, 20.0
        )
        self.assertIn(0.0, heights)
        self.assertTrue(all(-20 <= z <= 20 for z in heights))

    def test_far_panel_stays_unreachable(self):
        heights = contact_height_candidates(
            150.0, 200.0, -6.75308, 7.24692, -20.0, 20.0
        )
        self.assertEqual(heights, ())

    def test_narrow_reachable_interval(self):
        heights = contact_height_candidates(
            20.0, 21.0, -6.75308, 7.24692, -20.0, 20.0
        )
        self.assertTrue(heights)
        self.assertGreaterEqual(min(heights), 20.0 - 7.24692 - 1e-6)

    def test_preferred_height_clamped_and_duplicate_free(self):
        heights = contact_height_candidates(
            -200.0, 200.0, -7.0, 7.0, -20.0, 20.0,
            preferred_z_mm=100.0,
        )
        self.assertEqual(heights[0], 20.0)
        self.assertEqual(len(heights), len(set(heights)))

    def test_invalid_bounds_fail_closed(self):
        with self.assertRaises(ValueError):
            contact_height_candidates(0, 100, 8, -8, -20, 20)

    def test_tilted_section_uses_local_rear_not_global_bbox(self):
        polygon = ((500.0, 200.0), (300.0, 0.0),
                   (298.0, 0.0), (498.0, 200.0))
        rear = rear_edge_at_face_height(polygon, -2.0, 2.0)
        self.assertIsNotNone(rear)
        self.assertLess(rear, 310.0)

    def test_section_outside_height_returns_none(self):
        polygon = ((500.0, 200.0), (510.0, 200.0),
                   (510.0, 250.0), (500.0, 250.0))
        self.assertIsNone(rear_edge_at_face_height(polygon, -20.0, 20.0))


if __name__ == "__main__":
    unittest.main()
