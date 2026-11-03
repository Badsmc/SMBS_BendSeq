"""Unit checks for SheetMetal angle conversion; no FreeCAD runtime required."""
import unittest

from freecad_smbs.integration.sheetmetal_unfold_reader import (
    UnfoldReadError,
    fold_angle_from_sheetmetal,
)


class SheetMetalAngleTests(unittest.TestCase):
    def test_confirmed_examples(self):
        for source, expected in ((135, 45), (120, 60), (-150, -30), (90, 90), (-90, -90)):
            with self.subTest(source=source):
                self.assertAlmostEqual(fold_angle_from_sheetmetal(source), expected)

    def test_unsupported_values_fail_closed(self):
        for source in (0, 180, -180, float("nan"), float("inf")):
            with self.subTest(source=source):
                with self.assertRaises(UnfoldReadError):
                    fold_angle_from_sheetmetal(source)


if __name__ == "__main__":
    unittest.main()
