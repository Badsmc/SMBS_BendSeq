"""Regression tests for the shared authoritative component/whole-sheet path."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeSheetComponents, NativeBendSolidError, build_multi_bend_sheet_solid,
)


class FakeSolid:
    def __init__(self, volume):
        self.Volume = volume
        self.Solids = [self]
        self.calls = 0
        self.joined = None

    def isNull(self):
        return False

    def isValid(self):
        return True

    def isClosed(self):
        return True

    def multiFuse(self, others):
        self.calls += 1
        self.last_other_count = len(others)
        return self.joined


class ComponentSheetExtractionTests(unittest.TestCase):
    def test_named_shapes_keep_stable_panel_and_zone_identity(self):
        comp = NativeSheetComponents((("P01", object()),), (("bend_zone:B01", object()),), 2.0)
        self.assertEqual([name for name, _ in comp.named_shapes()], ["P01", "bend_zone:B01"])

    def test_complete_builder_uses_same_component_solids(self):
        left = FakeSolid(1.0)
        zone = FakeSolid(2.0)
        joined = FakeSolid(3.0)
        left.joined = joined
        components = NativeSheetComponents((("P01", left),), (("bend_zone:B01", zone),), 2.0)
        state = SimpleNamespace(angles={"B01": 90.0}, ordered_bends=[object()])
        with patch("smbs_core.kinematics2d.native_bend_solid_3d.build_multi_bend_sheet_components",
                   return_value=components) as extractor:
            result = build_multi_bend_sheet_solid(state, object(), segments=64)
        self.assertIs(result.shape, joined)
        self.assertEqual(result.panels_count, 1)
        self.assertEqual(result.bends_count, 1)
        self.assertEqual(left.calls, 1)
        self.assertEqual(left.last_other_count, 1)
        extractor.assert_called_once()

    def test_invalid_full_union_still_rejected(self):
        left = FakeSolid(1.0)
        zone = FakeSolid(2.0)
        bad_union = FakeSolid(3.0)
        bad_union.isValid = lambda: False
        left.joined = bad_union
        components = NativeSheetComponents((("P01", left),), (("bend_zone:B01", zone),), 2.0)
        state = SimpleNamespace(angles={"B01": 90.0}, ordered_bends=[object()])
        with patch("smbs_core.kinematics2d.native_bend_solid_3d.build_multi_bend_sheet_components",
                   return_value=components):
            with self.assertRaises(NativeBendSolidError):
                build_multi_bend_sheet_solid(state, object())

    def test_wrong_volume_still_rejected(self):
        left = FakeSolid(1.0)
        zone = FakeSolid(2.0)
        left.joined = FakeSolid(4.0)
        components = NativeSheetComponents((("P01", left),), (("bend_zone:B01", zone),), 2.0)
        state = SimpleNamespace(angles={"B01": 90.0}, ordered_bends=[object()])
        with patch("smbs_core.kinematics2d.native_bend_solid_3d.build_multi_bend_sheet_components",
                   return_value=components):
            with self.assertRaises(NativeBendSolidError):
                build_multi_bend_sheet_solid(state, object())


if __name__ == "__main__":
    unittest.main()
