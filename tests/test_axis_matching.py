import unittest

from smbs_core.kinematics2d.axis_matching import BendMatchError, match_bend_axes


class AxisMatchingTests(unittest.TestCase):
    def test_reversed_and_reordered(self):
        bends = [((0, 0), (10, 0)), ((0, 4), (10, 4))]
        sketch = [((10, 4), (0, 4)), ((10, 0), (0, 0))]
        self.assertEqual([m.sketch_index for m in match_bend_axes(bends, sketch)], [1, 0])

    def test_trimmed_2134_pattern(self):
        bends = [((0, 0), (498.3, 0)), ((0, 10), (498.3, 10)),
                 ((0, 20), (296.6, 20))]
        sketch = [((0, 0), (498.177582, 0)), ((0.122418, 10), (498.3, 10)),
                  ((0.122418, 20), (296.477582, 20))]
        result = match_bend_axes(bends, sketch)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[2].sketch_length_mm, 296.355164)

    def test_reject_parallel_offset(self):
        with self.assertRaises(BendMatchError):
            match_bend_axes([((0, 0), (10, 0))], [((0, 0.1), (10, 0.1))])

    def test_reject_duplicate(self):
        with self.assertRaises(BendMatchError):
            match_bend_axes([((0, 0), (10, 0)), ((0, 0), (10, 0))],
                            [((0, 0), (10, 0)), ((0, 2), (10, 2))])

    def test_reject_short_overlap(self):
        with self.assertRaises(BendMatchError):
            match_bend_axes([((0, 0), (10, 0))], [((0, 0), (2, 0))])

    def test_reject_invalid_tolerance(self):
        with self.assertRaises(BendMatchError):
            match_bend_axes([((0, 0), (10, 0))], [((0, 0), (10, 0))],
                            axis_tolerance_mm=-1)


if __name__ == "__main__":
    unittest.main()
