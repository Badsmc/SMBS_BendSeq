"""Exact native joined-sheet qualification must not repeat OCC BRep scans.

Tests the production qualification helper with an isolated in-memory object.
No mock may authorize changing physical collision semantics or bypassing a
full native isValid/isClosed/one-solid/positive-volume/union-volume test.
"""
import math
import unittest

from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeBendSolidError, _validated_joined_sheet_volume,
)


class CountedJoined:
    def __init__(self, *, null=False, valid=True, closed=True,
                 count=1, volume=100.0):
        self.null, self.valid, self.closed = null, valid, closed
        self.count, self.volume = count, volume
        self.calls = dict(isNull=0, isValid=0, isClosed=0, Solids=0, Volume=0)

    def isNull(self):
        self.calls["isNull"] += 1
        return self.null

    def isValid(self):
        self.calls["isValid"] += 1
        return self.valid

    def isClosed(self):
        self.calls["isClosed"] += 1
        return self.closed

    @property
    def Solids(self):
        self.calls["Solids"] += 1
        return [object()] * self.count

    @property
    def Volume(self):
        self.calls["Volume"] += 1
        return self.volume


class JoinedSheetQualificationReuseTests(unittest.TestCase):
    def test_good_joined_sheet_checks_native_once_only(self):
        joined = CountedJoined(volume=295547.88)
        self.assertEqual(_validated_joined_sheet_volume(joined), 295547.88)
        self.assertEqual(joined.calls, {
            "isNull": 1,
            "isValid": 1,
            "isClosed": 1,
            "Solids": 1,
            "Volume": 1,
        })

    def test_closed_and_topology_checks_are_not_skipped(self):
        for options in (
            {"null": True},
            {"valid": False},
            {"closed": False},
            {"count": 0},
            {"count": 2},
            {"volume": 0.0},
            {"volume": -2.0},
            {"volume": float("nan")},
            {"volume": float("inf")},
        ):
            with self.subTest(**options):
                joined = CountedJoined(**options)
                with self.assertRaisesRegex(
                    NativeBendSolidError,
                    "zones_and_panels_not_one_valid_closed_solid",
                ):
                    _validated_joined_sheet_volume(joined)
                self.assertEqual(joined.calls["isNull"], 1)

    def test_invalid_shape_does_not_get_a_second_verdict(self):
        joined = CountedJoined(valid=False)
        with self.assertRaises(NativeBendSolidError):
            _validated_joined_sheet_volume(joined)
        self.assertEqual(joined.calls["isValid"], 1)
        self.assertEqual(joined.calls["isClosed"], 0)

    def test_freecad_geometry_errors_still_propagate_fail_closed(self):
        class BrokenOCC(CountedJoined):
            def isValid(self):
                raise RuntimeError("OCC BRep check failed")
        with self.assertRaisesRegex(RuntimeError, "OCC BRep check failed"):
            _validated_joined_sheet_volume(BrokenOCC())


if __name__ == "__main__":
    unittest.main()
