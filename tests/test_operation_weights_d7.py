"""D7 operation weights and physically gated weighted solver tests."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.operation_cost import (
    OperationWeights, BendSetup, bend_length_mm, score_sequence,
    smallest_turn_deg, transition_penalty,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.sequence_search import (
    Feasibility, StepEvidence, REQUIRED_SCOPES, SearchStatus,
)
from smbs_core.kinematics2d.weighted_sequence_search import plan_weighted_forward


def fixture():
    graph = PanelGraph(("P0","P1","P2","P3"), (
        GraphEdge("B1","P0","P1",90.0, ((0,0,0),(100,0,0))),
        GraphEdge("B2","P1","P2",-90.0, ((0,20,0),(120,20,0))),
        GraphEdge("B3","P2","P3",90.0, ((0,40,0),(100,40,0))),
    ))
    return FoldState(graph, "P0")


def setups():
    return {
        "B1": BendSetup("B1", 0, False, 100),
        "B2": BendSetup("B2", 180, True, 120),
        "B3": BendSetup("B3", 0, False, 100),
    }


def certified(*_):
    return StepEvidence(Feasibility.CLEAR, "synthetic_test_only", REQUIRED_SCOPES)


class PenaltyTests(unittest.TestCase):
    def test_bend_line_length_from_sheetmetal_axis(self):
        b = SimpleNamespace(axis_xyz=((0,0,0),(30,40,0)))
        self.assertEqual(bend_length_mm(b), 50)

    def test_turn_wrap_and_no_motion(self):
        self.assertEqual(smallest_turn_deg(350,10),20)
        self.assertEqual(smallest_turn_deg(10,10),0)

    def test_first_bend_has_no_changeover(self):
        self.assertEqual(transition_penalty(None, setups()["B2"]).total,0)

    def test_all_three_penalties_are_independent(self):
        a = setups()["B1"]
        b = setups()["B2"]
        r = transition_penalty(a,b)
        self.assertEqual(r.turn_deg,180)
        self.assertTrue(r.is_flipped)
        self.assertEqual(r.length_delta_mm,20)
        self.assertGreater(r.turn_cost,0)
        self.assertGreater(r.flip_cost,0)
        self.assertGreater(r.length_change_cost,0)

    def test_no_cost_to_bend_angle_sign_alone(self):
        self.assertEqual(
            transition_penalty(BendSetup("B1",0,False,100),
                               BendSetup("B2",0,False,100)).total,0
        )

    def test_length_change_penalized_but_not_first_bend(self):
        a = BendSetup("B1",0,False,100)
        b = BendSetup("B2",0,False,150)
        p = transition_penalty(a,b)
        self.assertEqual(p.turn_cost,0)
        self.assertEqual(p.flip_cost,0)
        self.assertGreater(p.length_change_cost,0)

    def test_adding_future_weights_does_not_require_api_changes(self):
        weights = OperationWeights(flip=100,turn_fixed=0,length_change_fixed=0)
        scored=score_sequence(("B1","B2"),setups(),weights)
        self.assertGreaterEqual(scored.total,100)
        self.assertEqual(len(scored.to_dict()["steps"]),2)

    def test_missing_setup_rejected_instead_of_implicit_zero(self):
        with self.assertRaises(ValueError):
            score_sequence(("B1","B2"),{"B1":setups()["B1"]})

    def test_invalid_weights_rejected(self):
        with self.assertRaises(ValueError):
            OperationWeights(flip=-1)
        with self.assertRaises(ValueError):
            OperationWeights(turn_fixed=float("nan"))
        with self.assertRaises(ValueError):
            BendSetup("B1",0,False,0)

    def test_cost_optimizer_chooses_lower_over_first_feasible(self):
        initial=fixture()
        result=plan_weighted_forward(initial,certified,setups())
        self.assertEqual(result.status,SearchStatus.VERIFIED)
        self.assertEqual(result.sequence,("B1","B3","B2"))
        self.assertTrue(result.optimality_proven)
        self.assertEqual(result.cost.order,result.sequence)
        self.assertEqual(set(result.sequence),set(setups()))
        self.assertTrue(all(angle == 0 for angle in initial.angles.values()))

    def test_physical_collision_blocks_cheapest_path(self):
        def oracle(st,bid,prev):
            if bid=="B1" and prev is None:
                return StepEvidence(Feasibility.COLLISION,"fixture collision",REQUIRED_SCOPES)
            return certified()
        result=plan_weighted_forward(fixture(),oracle,setups())
        self.assertEqual(result.status,SearchStatus.VERIFIED)
        self.assertNotEqual(result.sequence[0],"B1")

    def test_unverified_low_cost_cannot_win_certified_ranking(self):
        def oracle(st,bid,prev):
            if bid=="B1" and prev is None:
                return StepEvidence(Feasibility.CLEAR,"partial",frozenset(("BENDING",)))
            return certified()
        result=plan_weighted_forward(
            fixture(),oracle,setups(),allow_partial_preview=True
        )
        self.assertEqual(result.status,SearchStatus.VERIFIED)
        self.assertTrue(result.success)
        self.assertNotEqual(result.sequence[0],"B1")

    def test_all_preview_only_never_certifies(self):
        partial=lambda *_: StepEvidence(Feasibility.CLEAR,"preview",frozenset(("BENDING",)))
        result=plan_weighted_forward(
            fixture(),partial,setups(),allow_partial_preview=True
        )
        self.assertEqual(result.status,SearchStatus.UNVERIFIED)
        self.assertFalse(result.success)
        self.assertEqual(result.sequence,())
        self.assertTrue(result.candidate_sequence)

    def test_budget_does_not_claim_optimality(self):
        result=plan_weighted_forward(fixture(),certified,setups(),max_states=1)
        self.assertEqual(result.status,SearchStatus.BUDGET_EXHAUSTED)
        self.assertFalse(result.optimality_proven)
        self.assertFalse(result.success)

    def test_rotation_and_flip_not_inferred_from_bend_sign(self):
        state=fixture()
        # The bend B2 is negative but has an independently specified flip.
        self.assertEqual(state.bends["B2"].angle_deg,-90)
        self.assertTrue(setups()["B2"].flipped)
        with self.assertRaises(ValueError):
            plan_weighted_forward(state,certified,{"B1":setups()["B1"]})


if __name__=="__main__":
    unittest.main()
