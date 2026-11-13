"""Headless contracts for immutable and bounded native OCC bend-zone caching."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeBendSolid, _cached_zone_solid,
)
from smbs_core.kinematics2d.native_geometry_build_metrics import (
    count_native_geometry_builds,
)
from smbs_core.kinematics2d.panel_kinematics import RigidTransform


class DummyShape:
    def __init__(self, label):
        self.label = label

    def copy(self):
        # In the real FreeCAD environment each copy owns a distinct Part.Shape.
        return DummyShape(self.label)


def fake_builder(state, bid, *, segments):
    return NativeBendSolid(
        bend_id=bid,
        angle_deg=state.angles[bid],
        shape=DummyShape("{}:{}:{}".format(
            bid, state.angles[bid], segments,
        )),
        volume_mm3=2.5,
        bend_line_length_mm=100.,
        section_area_mm2=0.025,
        facets_approximation=segments,
    )


def state_with_bend():
    state = SimpleNamespace(
        angles={"B01": 0.0},
        transforms={"P01": RigidTransform()},
    )
    bend = SimpleNamespace(
        bend_id="B01", fixed_panel="P01", moving_left=True,
        bend_zone=("B01", 1.5, 0.31),
    )
    return state, bend


class NativeZoneCacheTests(unittest.TestCase):
    def test_static_zone_is_built_once_and_returned_as_independent_copies(self):
        state, bend = state_with_bend()
        cache = {}
        with patch(
            "smbs_core.kinematics2d.native_bend_solid_3d.build_bend_zone_solid",
            side_effect=fake_builder,
        ) as builder, count_native_geometry_builds() as metrics:
            original = _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
            )
            reused = _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
            )
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(len(cache), 1)
        self.assertIsNot(original.shape, reused.shape)
        self.assertIsNot(cache[next(iter(cache))].shape, original.shape)
        self.assertIsNot(cache[next(iter(cache))].shape, reused.shape)
        self.assertEqual(reused.volume_mm3, original.volume_mm3)
        self.assertEqual(metrics.to_dict()["cache_events"]["bend_zone_cache_hit"], 1)
        self.assertEqual(metrics.to_dict()["cache_events"]["bend_zone_cache_miss"], 1)

    def test_pose_angle_zone_and_segments_change_invalidate_entry(self):
        state, bend = state_with_bend()
        cache = {}
        with patch(
            "smbs_core.kinematics2d.native_bend_solid_3d.build_bend_zone_solid",
            side_effect=fake_builder,
        ) as builder:
            _cached_zone_solid(state, bend, segments=128, zone_shape_cache=cache)
            state.angles["B01"] = 45.0
            _cached_zone_solid(state, bend, segments=128, zone_shape_cache=cache)
            state.transforms["P01"] = RigidTransform(
                translation=(100.0, 0.0, 0.0),
            )
            _cached_zone_solid(state, bend, segments=128, zone_shape_cache=cache)
            _cached_zone_solid(state, bend, segments=64, zone_shape_cache=cache)
            bend.bend_zone = ("B01", 1.5, 0.42)
            _cached_zone_solid(state, bend, segments=64, zone_shape_cache=cache)
            bend.moving_left = False
            _cached_zone_solid(state, bend, segments=64, zone_shape_cache=cache)
        self.assertEqual(builder.call_count, 6)
        self.assertEqual(len(cache), 6)

    def test_bounded_lru_evicts_oldest_entry_and_rebuilds_it(self):
        state, bend = state_with_bend()
        cache = {}
        with patch(
            "smbs_core.kinematics2d.native_bend_solid_3d.build_bend_zone_solid",
            side_effect=fake_builder,
        ) as builder, count_native_geometry_builds() as metrics:
            state.angles["B01"] = 0.0
            _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
                max_cache_entries=2,
            )
            state.angles["B01"] = 30.0
            _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
                max_cache_entries=2,
            )
            state.angles["B01"] = 60.0
            _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
                max_cache_entries=2,
            )
            self.assertEqual(len(cache), 2)
            state.angles["B01"] = 0.0
            _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=cache,
                max_cache_entries=2,
            )
        self.assertEqual(builder.call_count, 4)
        self.assertEqual(len(cache), 2)
        self.assertEqual(metrics.to_dict()["cache_events"]["bend_zone_cache_eviction"], 2)

    def test_disable_cache_keeps_existing_direct_builder_semantics(self):
        state, bend = state_with_bend()
        with patch(
            "smbs_core.kinematics2d.native_bend_solid_3d.build_bend_zone_solid",
            side_effect=fake_builder,
        ) as builder:
            a = _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=None,
            )
            b = _cached_zone_solid(
                state, bend, segments=128, zone_shape_cache=None,
            )
        self.assertEqual(builder.call_count, 2)
        self.assertIsNot(a.shape, b.shape)


if __name__ == "__main__":
    unittest.main()
