"""Pure Python smoke/regression tests for exploratory 2D kinematics."""
import math
import unittest

from smbs_core.kinematics2d import (
    Point2D as P, Segment2D as S, Bend2D as B,
    SectionState, fold_section, intersect_segments, self_intersections,
)


class TestKinematics2D(unittest.TestCase):
    def assert_point(self, actual, x, y):
        self.assertAlmostEqual(actual.x, x, places=8)
        self.assertAlmostEqual(actual.y, y, places=8)

    def test_signed_90_degree_rotation_and_immutability(self):
        original = SectionState((S(P(0, 0), P(10, 0), "base"),
                                 S(P(10, 0), P(20, 0), "flange")))
        folded = fold_section(original, B("B1", P(10, 0), ("flange",), math.pi/2))
        self.assert_point(folded.segments[1].b, 10, 10)
        self.assert_point(original.segments[1].b, 20, 0)
        self.assertEqual(folded.applied_bends, ("B1",))
        with self.assertRaises(ValueError):
            fold_section(folded, B("B1", P(10, 0), ("flange",), math.pi/2))

    def test_opposite_45_degree_angles(self):
        state = SectionState((S(P(0, 0), P(10, 0), "left"),
                              S(P(0, 0), P(10, 0), "right")))
        positive = fold_section(state, B("B+", P(0, 0), ("left",), math.pi/4))
        negative = fold_section(positive, B("B-", P(0, 0), ("right",), -math.pi/4))
        self.assertGreater(negative.segments[0].b.y, 0)
        self.assertLess(negative.segments[1].b.y, 0)

    def test_intersection_candidates_include_hinges(self):
        a = S(P(0, 0), P(10, 10), "a")
        b = S(P(0, 10), P(10, 0), "b")
        self.assertTrue(intersect_segments(a, b))
        self.assertEqual(self_intersections(SectionState((a, b))), (("a", "b"),))
        self.assertTrue(intersect_segments(a, S(P(10, 10), P(20, 10), "c")))
        self.assertFalse(intersect_segments(a, S(P(11, 10), P(20, 10), "c")))

    def test_unknown_panels_rejected(self):
        state = SectionState((S(P(0, 0), P(1, 0), "a"),))
        with self.assertRaises(ValueError):
            fold_section(state, B("B", P(0, 0), ("missing",), 1.0))


if __name__ == "__main__":
    unittest.main()
