"""D6.1 deterministic DFS and verified-order adapter regression.

Synthetic step oracle is TEST ONLY: cannot establish production feasibility.
"""
import unittest

from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.fold_sequence import BendSequenceController
from smbs_core.kinematics2d.sequence_search import (
    Feasibility, SearchStatus, REQUIRED_SCOPES, StepEvidence,
    plan_forward, apply_verified_order,
)


def fixture():
    graph = PanelGraph(("P1", "P2", "P3", "P4"), (
        GraphEdge("B1", "P1", "P2", 90.0, ((0, 0, 0), (10, 0, 0))),
        GraphEdge("B2", "P2", "P3", -45.0, ((0, 10, 0), (10, 10, 0))),
        GraphEdge("B3", "P3", "P4", 60.0, ((0, 20, 0), (10, 20, 0))),
    ))
    return FoldState(graph, "P1")


def clear_all(*_):
    return StepEvidence(Feasibility.CLEAR, "synthetic_test", REQUIRED_SCOPES)


class SequenceSearchTests(unittest.TestCase):
    def test_finds_alternative_order_after_collision(self):
        state = fixture()
        before_angles = dict(state.angles)
        before_transforms = state.snapshot()
        calls = []

        def oracle(current, bid, previous):
            calls.append((frozenset(k for k,v in current.angles.items() if abs(v)>0.1),
                          bid, previous))
            if bid == "B1" and previous is None:
                return StepEvidence(Feasibility.COLLISION, "B1 cannot be first")
            return clear_all()
        result = plan_forward(state, oracle)
        self.assertEqual(result.status, SearchStatus.VERIFIED)
        self.assertEqual(result.sequence[0], "B2")
        self.assertEqual(set(result.sequence), {"B1", "B2", "B3"})
        self.assertEqual(before_angles, state.angles)
        self.assertEqual(before_transforms, state.snapshot())
        self.assertTrue(any(previous == "B2" for _,_,previous in calls))

    def test_unknown_never_becomes_verified(self):
        result = plan_forward(fixture(), lambda *_: StepEvidence(
            Feasibility.UNVERIFIED, "missing_backgauge_geometry"
        ))
        self.assertEqual(result.status, SearchStatus.UNVERIFIED)
        self.assertFalse(result.success)
        self.assertEqual(result.sequence, ())

    def test_clear_with_missing_scope_is_unverified(self):
        incomplete = REQUIRED_SCOPES - {"MACHINE"}
        result = plan_forward(fixture(), lambda *_: StepEvidence(
            Feasibility.CLEAR, "2D only", incomplete))
        self.assertEqual(result.status, SearchStatus.UNVERIFIED)
        self.assertIn("missing_scopes:MACHINE", result.rejected[0][1])

    def test_all_conclusive_collisions(self):
        result = plan_forward(fixture(), lambda *_: StepEvidence(
            Feasibility.COLLISION, "actual_test_collision", REQUIRED_SCOPES))
        self.assertEqual(result.status, SearchStatus.NO_FEASIBLE_SEQUENCE)

    def test_2d_only_collision_cannot_disprove_all_orders(self):
        result = plan_forward(fixture(), lambda *_: StepEvidence(
            Feasibility.COLLISION, "suspected_2d_collision"))
        self.assertEqual(result.status, SearchStatus.UNVERIFIED)
        self.assertFalse(result.success)

    def test_budget_exhaustion_not_no_solution(self):
        result = plan_forward(fixture(), clear_all, max_states=1)
        self.assertEqual(result.status, SearchStatus.BUDGET_EXHAUSTED)

    def test_oracle_exception_does_not_certify(self):
        def failing(*_):
            raise RuntimeError("backend unavailable")
        result = plan_forward(fixture(), failing)
        self.assertEqual(result.status, SearchStatus.UNVERIFIED)
        self.assertTrue(any("oracle_error" in x[1] for x in result.rejected))

    def test_nonflat_input_rejected_without_mutation(self):
        state = fixture()
        state.set_angle("B1", 90)
        before = dict(state.angles)
        result = plan_forward(state, clear_all)
        self.assertEqual(result.status, SearchStatus.UNVERIFIED)
        self.assertEqual(state.angles, before)

    def test_verified_result_applies_existing_controller_order(self):
        state = fixture()
        controller = BendSequenceController(state)
        result = plan_forward(state, clear_all)
        apply_verified_order(controller, result)
        self.assertEqual(controller.bend_order, result.sequence)

    def test_unverified_result_cannot_change_controller(self):
        state = fixture()
        controller = BendSequenceController(state)
        before = controller.bend_order
        result = plan_forward(state, lambda *_: StepEvidence(
            Feasibility.UNVERIFIED, "unknown"))
        with self.assertRaises(ValueError):
            apply_verified_order(controller, result)
        self.assertEqual(controller.bend_order, before)


if __name__ == "__main__":
    unittest.main()
