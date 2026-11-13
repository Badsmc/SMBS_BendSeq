"""Unit contracts for safe per-order OCC BRep reuse.

These tests cover cache ownership and invalidation, NOT native FreeCAD
collision equivalence. The latter requires the reference FCStd in FreeCAD.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_sequence_geometry_cache import (
    NativeSequenceGeometryCache,
)
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveTrajectoryValidator3D, AdaptiveSamplingConfig,
    _bounded_full_sheet_cache_put,
)


def make_validator(shared=None):
    dummy_die = SimpleNamespace(
        v_opening_mm=12.0, effective_v_opening_mm=12.0
    )
    return AdaptiveTrajectoryValidator3D(
        fold_state=object(), panel_build=object(),
        punch_base=object(), die=dummy_die,
        config=AdaptiveSamplingConfig(),
        sequence_geometry_cache=shared,
    )


class NativeSequenceGeometryCacheTests(unittest.TestCase):
    def test_cache_must_be_bounded_and_local_by_default(self):
        for invalid in (0, -1, 129, True, 0.5, "8"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    NativeSequenceGeometryCache(max_full_sheets=invalid)
        a = make_validator()
        b = make_validator()
        self.assertIsNot(a._solid_cache, b._solid_cache)
        self.assertIsNot(a._panel_prism_cache, b._panel_prism_cache)
        self.assertIsNot(a._zone_shape_cache, b._zone_shape_cache)

    def test_same_run_reuses_only_geometric_shapes_not_clearance_verdicts(self):
        shared = NativeSequenceGeometryCache(max_full_sheets=2)
        a = make_validator(shared)
        # Source binding happens before geometry construction in real D6.
        self.assertTrue(shared.bind_source(("part-A", "faces", 1.5)))
        a._panel_prism_cache_identity = shared.source_identity
        a._panel_prism_cache["P01"] = object()
        a._zone_shape_cache[("B07", 0.0)] = object()
        a._solid_cache[("angles", 0)] = object()
        a._pose_collision_cache[("status", 0)] = "CLEAR"
        a._die_clearance_cache[("die", 0)] = "CLEAR"
        b = make_validator(shared)
        self.assertIs(a._solid_cache, b._solid_cache)
        self.assertIs(a._panel_prism_cache, b._panel_prism_cache)
        self.assertIs(a._zone_shape_cache, b._zone_shape_cache)
        self.assertEqual(b._panel_prism_cache_identity,
                         ("part-A", "faces", 1.5))
        self.assertIn("P01", b._panel_prism_cache)
        self.assertIn(("angles", 0), b._solid_cache)
        self.assertEqual(b._pose_collision_cache, {})
        self.assertEqual(b._die_clearance_cache, {})
        self.assertEqual(b._punch_clearance_cache, {})

    def test_source_change_invalidates_every_shared_geometry_entry(self):
        shared = NativeSequenceGeometryCache(max_full_sheets=2)
        self.assertTrue(shared.bind_source(("part-A",)))
        shared.full_sheets[("a",)] = object()
        shared.panel_prisms["P01"] = object()
        shared.bend_zones["B07"] = object()
        self.assertFalse(shared.bind_source(("part-A",)))
        self.assertEqual(len(shared.full_sheets), 1)
        self.assertTrue(shared.bind_source(("part-B",)))
        self.assertEqual(shared.full_sheets, {})
        self.assertEqual(shared.panel_prisms, {})
        self.assertEqual(shared.bend_zones, {})
        self.assertEqual(shared.source_resets, 2)
        self.assertEqual(shared.source_identity, ("part-B",))

    def test_clear_on_validator_invalidation_and_no_stale_full_sheets(self):
        shared = NativeSequenceGeometryCache(max_full_sheets=2)
        shared.bind_source(("part-A",))
        one = make_validator(shared)
        _bounded_full_sheet_cache_put(
            one._solid_cache, (1,), "shape-1", one._max_cached_full_sheets
        )
        _bounded_full_sheet_cache_put(
            one._solid_cache, (2,), "shape-2", one._max_cached_full_sheets
        )
        _bounded_full_sheet_cache_put(
            one._solid_cache, (3,), "shape-3", one._max_cached_full_sheets
        )
        self.assertEqual(list(shared.full_sheets), [(2,), (3,)])
        one.set_config(AdaptiveSamplingConfig(arc_segments=64))
        self.assertEqual(shared.full_sheets, {})
        self.assertIsNone(shared.source_identity)
        self.assertEqual(shared.statistics()["full_sheet_limit"], 2)

    def test_runs_are_isolated_from_each_other(self):
        a = NativeSequenceGeometryCache()
        b = NativeSequenceGeometryCache()
        a.bind_source(("A",))
        a.panel_prisms["P01"] = object()
        self.assertEqual(b.panel_prisms, {})
        self.assertIsNone(b.source_identity)


if __name__ == "__main__":
    unittest.main()
