"""Contract tests for verified machine-space tool extents; no FreeCAD required."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.verified_tool_spans import span_from_native_shape


def native_tool(a=-50.0, b=50.0, *, span=None, virtual=False, valid=True, volume=100):
    bb = SimpleNamespace(XMin=a, XMax=b)
    shape = SimpleNamespace(
        BoundBox=bb, Volume=volume,
        isNull=lambda: False, isValid=lambda: valid,
    )
    return SimpleNamespace(
        machine_shape=shape,
        span_xm=(a, b) if span is None else span,
        is_virtual_length=virtual,
    )


class VerifiedToolSpanTests(unittest.TestCase):
    def test_exact_machine_bbox_not_assumed_symmetric(self):
        self.assertEqual(
            span_from_native_shape(native_tool(12.0, 147.5), "PUNCH"),
            (12.0, 147.5),
        )

    def test_refuse_loader_span_mismatch(self):
        with self.assertRaisesRegex(ValueError, "disagree"):
            span_from_native_shape(
                native_tool(-20, 80, span=(-50, 50)), "DIE"
            )

    def test_refuse_virtual_length_even_with_plausible_bbox(self):
        with self.assertRaisesRegex(ValueError, "fixed-length"):
            span_from_native_shape(native_tool(virtual=True), "PUNCH")

    def test_refuse_unknown_geometry(self):
        obj = native_tool()
        obj.machine_shape = None
        with self.assertRaisesRegex(ValueError, "missing"):
            span_from_native_shape(obj, "PUNCH")

    def test_refuse_invalid_solid(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            span_from_native_shape(native_tool(valid=False), "DIE")
        with self.assertRaisesRegex(ValueError, "invalid"):
            span_from_native_shape(native_tool(volume=0), "DIE")

    def test_refuse_bad_machine_extents(self):
        with self.assertRaisesRegex(ValueError, "extents"):
            span_from_native_shape(native_tool(10, 10), "DIE")
        with self.assertRaisesRegex(ValueError, "extents"):
            span_from_native_shape(native_tool(float("nan"), 10), "DIE")

    def test_refuse_missing_loader_span(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            span_from_native_shape(native_tool(span=()), "PUNCH")


if __name__ == "__main__":
    unittest.main()
