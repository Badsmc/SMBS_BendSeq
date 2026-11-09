"""Headless tests for opt-in native sheet-construction instrumentation."""
import unittest

from smbs_core.kinematics2d.native_geometry_build_metrics import (
    count_native_geometry_builds,
    measure_native_geometry_region,
)


class NativeGeometryBuildMetricsTests(unittest.TestCase):
    def test_disabled_instrumentation_preserves_execution(self):
        with measure_native_geometry_region("sheet_union"):
            value = 10 + 7
        self.assertEqual(value, 17)

    def test_counts_all_named_regions_and_serializes(self):
        names = (
            "panel_extrude", "panel_transform", "bend_zone_build",
            "sheet_union", "sheet_qualification",
        )
        with count_native_geometry_builds() as metrics:
            for name in names:
                with measure_native_geometry_region(name):
                    pass
            with measure_native_geometry_region("panel_extrude"):
                pass
        data = metrics.to_dict()
        self.assertEqual(data["calls"]["panel_extrude"], 2)
        for name in names:
            self.assertGreaterEqual(data["elapsed_s"][name], 0)
            self.assertEqual(data["failures"][name], 0)
        self.assertEqual(data["scope"], "INSTRUMENTED_NATIVE_SHEET_CONSTRUCTION_ONLY")
        self.assertGreaterEqual(data["measured_total_s"], 0)

    def test_nested_contexts_are_isolated(self):
        with count_native_geometry_builds() as outer:
            with measure_native_geometry_region("panel_extrude"):
                pass
            with count_native_geometry_builds() as inner:
                with measure_native_geometry_region("sheet_union"):
                    pass
            with measure_native_geometry_region("panel_transform"):
                pass
        self.assertEqual(outer.calls.get("sheet_union", 0), 0)
        self.assertEqual(outer.calls["panel_extrude"], 1)
        self.assertEqual(outer.calls["panel_transform"], 1)
        self.assertEqual(inner.calls["sheet_union"], 1)

    def test_exception_propagates_and_is_measured(self):
        with count_native_geometry_builds() as metrics:
            with self.assertRaisesRegex(RuntimeError, "kernel"):
                with measure_native_geometry_region("sheet_union"):
                    raise RuntimeError("kernel")
        self.assertEqual(metrics.failures["sheet_union"], 1)
        self.assertEqual(metrics.calls["sheet_union"], 1)

    def test_reject_unknown_label(self):
        with self.assertRaises(ValueError):
            with measure_native_geometry_region("fictional_operation"):
                pass


if __name__ == "__main__":
    unittest.main()
