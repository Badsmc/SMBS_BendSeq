import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from freecad.bendseq.core.kinematics2d.auto_tool_apply import pinned_orientations, verify_candidate_layout

MODULE = "freecad.bendseq.core.kinematics2d.auto_tool_apply"


def op(name, bend=None, kind="SINGLE"):
    return NS(operation_id=name, bend_id=bend or name, kind=kind)


def candidate(order, pins):
    return NS(operation_ids=order, orientations=lambda: pins)


class AutoToolApplyTests(unittest.TestCase):
    def setUp(self):
        self.ops = (op("B01"), op("B02"))
        self.bends = {"B01": NS(axis_xyz=((0, 0, 0), (40, 0, 0))),
                      "B02": NS(axis_xyz=((60, 0, 0), (100, 0, 0)))}
        self.result = candidate(("B02", "B01"), {"B01": False, "B02": True})

    def test_pins_respect_operator_and_ignore_fast_advisory(self):
        self.assertEqual(pinned_orientations(self.ops, {"B01": True},
                                            {"B02": True}, {"B02"}), {"B01": True})

    def test_candidate_order_and_orientation_must_match(self):
        assembly = NS(punch_spans_xm=((-100, 100),), die_spans_xm=((-100, 100),))
        with self.assertRaisesRegex(ValueError, "locked"):
            verify_candidate_layout(self.result, self.ops, self.bends, assembly, {"B01": True})
        with self.assertRaisesRegex(ValueError, "exact"):
            verify_candidate_layout(candidate(("B01", "B01"), {}), self.ops, self.bends, assembly, {})

    def test_new_shared_stroke_requires_full_coverage(self):
        with patch(MODULE + ".continuous_strokes", return_value=(("B01", "B02"),)) as strokes:
            short = NS(punch_spans_xm=((-20, 20),), die_spans_xm=((-50, 50),))
            with self.assertRaisesRegex(ValueError, "cover FAST stroke"):
                verify_candidate_layout(self.result, self.ops, self.bends, short, {})
            long = NS(punch_spans_xm=((-50, 50),), die_spans_xm=((-50, 50),))
            self.assertEqual(verify_candidate_layout(self.result, self.ops, self.bends, long, {}),
                             ((("B01", "B02"), 100.0),))
            self.assertEqual(tuple(x.operation_id for x in strokes.call_args.args[0]), ("B02", "B01"))

    def test_staged_operations_use_physical_bend_ids(self):
        ops = (op("B01.P", "B01", "PREBEND"), op("B01.F", "B01", "FINISH"))
        c = candidate(("B01.P", "B01.F"), {"B01.P": True, "B01.F": False})
        assembly = NS(punch_spans_xm=((-20, 20),), die_spans_xm=((-20, 20),))
        self.assertEqual(verify_candidate_layout(c, ops, self.bends, assembly, {}),
                         ((("B01",), 40.0), (("B01",), 40.0)))
