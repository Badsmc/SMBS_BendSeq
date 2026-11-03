"""Unit checks for SheetMetal angle conversion; no FreeCAD runtime required."""
import unittest

from freecad_smbs.integration.sheetmetal_unfold_reader import (
    UnfoldReadError,
    _calculator,
    fold_angle_from_sheetmetal,
)


class _DummyUnfold:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


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

    def test_missing_or_invalid_kfactor_properties_fail_closed(self):
        import sys
        from unittest.mock import MagicMock
        with unittest.mock.patch.dict(sys.modules, {"SheetMetalNewUnfolder": MagicMock()}):
            with self.assertRaises(UnfoldReadError):
                _calculator(_DummyUnfold(KFactor=0.4))  # missing KFactorStandard
            with self.assertRaises(UnfoldReadError):
                _calculator(_DummyUnfold(KFactorStandard="ansi"))  # missing KFactor
            with self.assertRaises(UnfoldReadError):
                _calculator(_DummyUnfold(KFactor=0.4, KFactorStandard="unknown"))
            with self.assertRaises(UnfoldReadError):
                _calculator(_DummyUnfold(KFactor=1.5, KFactorStandard="ansi"))
            with self.assertRaises(UnfoldReadError):
                _calculator(_DummyUnfold(KFactor=-0.1, KFactorStandard="ansi"))


if __name__ == "__main__":
    unittest.main()
