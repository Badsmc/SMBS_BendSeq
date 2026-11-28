import unittest
from types import SimpleNamespace as NS
from freecad.bendseq.core.kinematics2d.auto_tool_retry import attempt_order, mesh_outcome, next_action


class AutoToolRetryTests(unittest.TestCase):
    def test_selected_first_distinct_and_bounded(self):
        def entry(p):
            return NS(punch_id=p, die_id="D"), object()
        a, b, c, d = map(entry, "ABCD")
        self.assertEqual(attempt_order(b, [a,b,c,d], retry=True), (b,a,c))
        self.assertEqual(attempt_order(b, [a,b], retry=False), (b,))

    def test_outcomes_are_fail_closed(self):
        order = ("B01", "B02")
        clear = dict(findings=[], conditional_findings=[], unresolved=[], pending=[],
                     completed=list(order), reason="", samples=30)
        self.assertEqual(mesh_outcome(clear, order), "SAMPLED_NO_COLLISION")
        self.assertEqual(mesh_outcome(dict(clear, samples=0), order), "INCOMPLETE")
        self.assertEqual(mesh_outcome(dict(clear, unresolved=[{}]), order), "INCOMPLETE")
        self.assertEqual(mesh_outcome(dict(clear, conditional_findings=[{}]), order), "CONDITIONAL")
        self.assertEqual(mesh_outcome(dict(clear, completed=["B01"], pending=["B02"],
                                           reason="TIME_BUDGET"), order), "INCOMPLETE")
        self.assertEqual(mesh_outcome(dict(clear, findings=[{"operation":"B01"}]), order),
                         "CONFIRMED_COLLISION")
        self.assertEqual(mesh_outcome(dict(clear, findings=[{"operation":"B03"}]), order), "ERROR")
        self.assertEqual(mesh_outcome(dict(clear, reason="ValueError: bad"), order), "ERROR")
        self.assertEqual(mesh_outcome(None, order), "ERROR")

    def test_only_confirmed_collision_retries(self):
        self.assertEqual(next_action("CONFIRMED_COLLISION", 2, 30), "RETRY")
        self.assertEqual(next_action("CONFIRMED_COLLISION", 0, 30), "ALTERNATIVES_EXHAUSTED")
        self.assertEqual(next_action("CONFIRMED_COLLISION", 1, 0), "TOTAL_BUDGET_EXCEEDED")
        for status in ("INCOMPLETE", "CONDITIONAL", "ERROR", "SAMPLED_NO_COLLISION"):
            self.assertEqual(next_action(status, 2, 30), status)
