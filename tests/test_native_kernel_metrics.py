"""Headless tests for exact collision-kernel instrumentation."""
import unittest

from smbs_core.kinematics2d.native_kernel_metrics import (
    count_native_kernel_calls, measured_common, measured_distance,
)


class DummyShape:
    def __init__(self, fail=False):
        self.fail = fail

    def common(self, other):
        if self.fail:
            raise RuntimeError("native error")
        return ("common", other)

    def distToShape(self, other):
        return (2.0, other)


class NativeKernelMetricsTests(unittest.TestCase):
    def test_disabled_calls_keep_original_behavior(self):
        obj = DummyShape()
        self.assertEqual(measured_common(obj, "b", label="pair_common"),
                         obj.common("b"))
        self.assertEqual(measured_distance(obj, "b", label="full_shape_distance"),
                         obj.distToShape("b"))

    def test_common_and_distance_are_counted_separately(self):
        obj = DummyShape()
        with count_native_kernel_calls() as counter:
            measured_common(obj, "b", label="pair_common")
            measured_distance(obj, "b", label="full_shape_distance")
            measured_distance(obj, "b", label="full_shape_distance")
        result = counter.to_dict()
        self.assertEqual(result["common_calls"], 1)
        self.assertEqual(result["distance_calls"], 2)
        self.assertEqual(result["total_instrumented_calls"], 3)
        self.assertEqual(result["labels"]["full_shape_distance"], 2)
        self.assertGreaterEqual(result["total_elapsed_s"], 0)

    def test_kernel_failure_still_counted_and_propagated(self):
        with count_native_kernel_calls() as counter:
            with self.assertRaisesRegex(RuntimeError, "native error"):
                measured_common(DummyShape(fail=True), "b", label="pair_common")
        self.assertEqual(counter.common_calls, 1)
        self.assertEqual(counter.failures, 1)

    def test_nested_scopes_do_not_leak(self):
        obj = DummyShape()
        with count_native_kernel_calls() as outer:
            measured_common(obj, "b", label="outer")
            with count_native_kernel_calls() as inner:
                measured_distance(obj, "b", label="inner")
            measured_common(obj, "b", label="outer")
        self.assertEqual(outer.common_calls, 2)
        self.assertEqual(outer.distance_calls, 0)
        self.assertEqual(inner.distance_calls, 1)
        self.assertNotIn("inner", outer.labels)

    def test_context_restored_after_exception(self):
        with self.assertRaises(RuntimeError):
            with count_native_kernel_calls() as counter:
                measured_common(DummyShape(), "b", label="a")
                raise RuntimeError("stop")
        self.assertEqual(counter.common_calls, 1)
        with count_native_kernel_calls() as next_counter:
            measured_common(DummyShape(), "b", label="b")
        self.assertEqual(next_counter.labels, {"b": 1})


if __name__ == "__main__":
    unittest.main()
