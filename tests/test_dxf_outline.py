import unittest

from freecad_smbs.integration.dxf_manual import join_outline


class DxfOutlineTests(unittest.TestCase):
    def test_small_gap_can_be_joined_with_explicit_tolerance(self):
        contour = [
            ((0, 0), (10, 0)),
            ((10, 0), (10, 10)),
            ((10, 10), (0, 10)),
            ((0, 10), (0, 0.035)),
        ]
        with self.assertRaisesRegex(ValueError, r"расстояние 0\.035 мм"):
            join_outline(contour)
        self.assertEqual(join_outline(contour, tolerance=0.05)[-1], ((0, 10), (0, 0)))

    def test_open_contour_reports_nearest_open_endpoint(self):
        contour = [((0, 0), (10, 0)), ((10, 0), (10, 10))]
        with self.assertRaisesRegex(ValueError, r"X=10\.000, Y=10\.000.*14\.142 мм"):
            join_outline(contour, tolerance=0.5)
