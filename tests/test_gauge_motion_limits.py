import unittest
from freecad.bendseq.core.kinematics2d.gauge_motion_limits import (
    bounded_gauge_height, flange_below_opening, gauge_transfer,
)


class GaugeMotionTest(unittest.TestCase):
    bounds = (-5, 20, -3, 3, -4, 5)
    die = ((-10, -30), (10, -30), (10, 0), (-10, 0))

    def limit(self, **kwargs):
        params = dict(requested_z=-10, preferred_z=-10, z_min=-20, z_max=20,
                      s_mm=12, stations=(0,), gauge_bounds=self.bounds,
                      face_z_span=(-2, 2), die_polygon=self.die, die_spans=((-100, 100),))
        params.update(kwargs)
        return bounded_gauge_height(**params)

    def test_body_over_die_cannot_descend_below_top(self):
        result = self.limit()
        self.assertTrue(result.overlaps_die)
        self.assertGreaterEqual(result.z_mm + self.bounds[4], 0)
        self.assertFalse(result.contact_possible)

    def test_no_overlap_in_depth_allows_lower_position(self):
        result = self.limit(s_mm=50)
        self.assertFalse(result.overlaps_die)
        self.assertEqual(result.z_mm, -10)
        self.assertTrue(result.contact_possible)

    def test_outside_die_length_allows_lower_position(self):
        self.assertEqual(self.limit(stations=(120,)).z_mm, -10)
        self.assertTrue(self.limit(stations=(120, 0)).overlaps_die)

    def test_previous_height_retained_until_face_cannot_reach_edge(self):
        self.assertEqual(self.limit(s_mm=50, requested_z=1, preferred_z=0).z_mm, 0)
        self.assertEqual(self.limit(s_mm=50, requested_z=7, preferred_z=0).z_mm, 5)

    def test_range_limits_and_impossible_die_clearance(self):
        self.assertEqual(self.limit(s_mm=50, requested_z=100).z_mm, 20)
        self.assertIsNone(self.limit(z_max=3).z_mm)
        with self.assertRaises(ValueError):
            self.limit(requested_z=float("nan"))

    def test_nominal_flange_warning_rule_and_unknown_opening(self):
        self.assertTrue(flange_below_opening(10, 12))
        for flange, opening in ((12, 12), (13, 12), (10, None), (None, 12), (10, 'NOT_SPECIFIED')):
            self.assertFalse(flange_below_opening(flange, opening))

    def test_transfer_raises_before_crossing_and_lowers_after(self):
        opts = dict(gauge_bounds=self.bounds, face_z_span=(-2, 2),
                    die_polygon=self.die, die_spans=((-100, 100),))
        def at(t, max_z=20):
            return gauge_transfer((-50, -10), (50, -8), (0,), (0,), t, -20, max_z, **opts)
        self.assertEqual(at(0)[:2], (-50, -10))
        self.assertEqual(at(1)[:2], (50, -8))
        self.assertEqual(at(.1)[0], -50)
        self.assertEqual(at(.9)[0], 50)
        for i in range(20, 81):
            s, z, _ = at(i / 100)
            if s + self.bounds[1] >= -10 and s + self.bounds[0] <= 10:
                self.assertGreaterEqual(z + self.bounds[4], 0)
        self.assertIsNone(at(.5, max_z=3))
