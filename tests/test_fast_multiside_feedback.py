"""All-side FAST seeds and failed-transition-feedback regression tests."""
from __future__ import annotations

from math import cos, pi, sin
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.fast_side_seeds import (
    simplified_outer_sides, forced_side_seeds,
)
from smbs_core.kinematics2d.fast_multiside_feedback import (
    search_all_outer_sides, search_feedback_recovery,
    transition_feedback_penalties,
)
from smbs_core.kinematics2d.fast_bfm_beam_search import (
    FastCandidate, search_fast_candidates,
)
from smbs_core.kinematics2d.fast_shortlist_screening import ShortlistCandidateCheck
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import BendOperation


def sample_part(outline):
    n = len(outline)
    bends = []
    for i, a in enumerate(outline):
        b = outline[(i+1) % n]
        mid=((a[0]+b[0])/2, (a[1]+b[1])/2)
        # Parallel to each external edge, slightly inside the boundary.
        axis=((mid[0]*0.9-(b[0]-a[0])*0.22,
               mid[1]*0.9-(b[1]-a[1])*0.22, 0.),
              (mid[0]*0.9+(b[0]-a[0])*0.22,
               mid[1]*0.9+(b[1]-a[1])*0.22, 0.))
        bends.append(GraphEdge(f"B{i+1:02d}", f"P{i}", f"P{i+1}",
                               90., axis))
    state=FoldState(
        PanelGraph(tuple(f"P{i}" for i in range(n+1)),tuple(bends)),
        "P0",
    )
    operations=tuple(
        BendOperation(f"B{i+1:02d}", f"B{i+1:02d}", 90.)
        for i in range(n)
    )
    return state,operations


class SideSeedTests(unittest.TestCase):
    def test_square_four_named_sides_even_if_outline_split_and_closed(self):
        square=((0.,0.),(50.,0.),(100.,0.),(100.,50.),
                (100.,100.),(50.,100.),(0.,100.),
                (0.,50.),(0.,0.))
        state,ops=sample_part(((0.,0.),(100.,0.),(100.,100.),(0.,100.)))
        seeds=forced_side_seeds(square,ops,state)
        self.assertEqual(len(simplified_outer_sides(square)),4)
        self.assertEqual({x.name for x in seeds},
                         {"LEFT","RIGHT","TOP","BOTTOM"})
        self.assertTrue(all(s.axis_aligned for s in seeds))
        self.assertTrue(all(s.first_operation_ids for s in seeds))
        self.assertTrue(all(s.first_reverse for s in seeds))

    def test_hexagon_produces_six_independent_side_seeds(self):
        hexagon=tuple(
            (100*cos(2*pi*i/6),100*sin(2*pi*i/6)) for i in range(6)
        )
        state,ops=sample_part(hexagon)
        seeds=forced_side_seeds(hexagon,ops,state)
        self.assertEqual(len(seeds),6)
        self.assertEqual([seed.name for seed in seeds],
                         [f"SIDE_{i}" for i in range(1,7)])
        self.assertEqual(len({s.first_operation_ids[0] for s in seeds}),6)

    def test_uses_every_true_cad_edge_even_collinear_and_small_chamfers(self):
        # The five native edges contain two collinear authored CAD edges.
        # Neither should silently disappear in the forced-side schedule.
        outline=((0.,0.),(50.,0.),(100.,0.),(100.,99.8),
                 (99.8,100.),(0.,100.))
        cad=tuple((outline[i],outline[(i+1)%len(outline)])
                  for i in range(len(outline)))
        actual=simplified_outer_sides(
            outline, outer_edge_polylines_xy=cad,
        )
        self.assertEqual(len(actual),6)
        state,ops=sample_part(outline)
        seeds=forced_side_seeds(
            outline,ops,state,outer_edge_polylines_xy=cad,
        )
        self.assertEqual(len(seeds),6)
        self.assertEqual([entry.name for entry in seeds],
                         [f"SIDE_{i}" for i in range(1,7)])

    def test_really_uses_more_than_twenty_four_sides(self):
        n=30
        outline=tuple(
            (150*cos(2*pi*i/n),150*sin(2*pi*i/n)) for i in range(n)
        )
        cad=tuple((outline[i],outline[(i+1)%n]) for i in range(n))
        self.assertEqual(len(simplified_outer_sides(
            outline,outer_edge_polylines_xy=cad,
        )),30)
        state,ops=sample_part(outline)
        seeds=forced_side_seeds(
            outline,ops,state,outer_edge_polylines_xy=cad,
        )
        self.assertEqual(len(seeds),30)
        self.assertEqual(len({entry.name for entry in seeds}),30)

    def test_curved_cad_edge_sampled_by_direction_not_each_mesh_point(self):
        quarter=tuple(
            (100*cos((pi/2)*i/72),100*sin((pi/2)*i/72))
            for i in range(73)
        )
        outline=((0.,0.),)+quarter
        cad=(
            ((0.,0.),(100.,0.)),
            quarter,
            ((0.,100.),(0.,0.)),
        )
        sides=simplified_outer_sides(
            outline, outer_edge_polylines_xy=cad,
        )
        self.assertGreater(len(sides),3)
        self.assertLess(len(sides),len(quarter))
        self.assertTrue(all(
            abs(side[3][0]**2+side[3][1]**2-1) < 1e-7
            for side in sides
        ))

    def test_fallback_polygon_preserves_real_short_chamfer(self):
        outline=((0.,0.),(100.,0.),(100.,99.8),
                 (99.8,100.),(0.,100.))
        self.assertEqual(len(simplified_outer_sides(outline)),5)

    def test_reverse_orientation_depends_on_which_outer_side_faces_operator(self):
        square=((-50.,-50.),(50.,-50.),(50.,50.),(-50.,50.))
        state,ops=sample_part(square)
        seeds={s.name:s for s in forced_side_seeds(square,ops,state)}
        # Any shared parallel bend has to face opposite directions when
        # we start from opposite sides.
        def first_reverse(name):
            return dict(seeds[name].first_reverse)
        bottom=first_reverse("BOTTOM")
        top=first_reverse("TOP")
        common=set(bottom).intersection(top)
        self.assertTrue(common)
        for key in common:
            self.assertNotEqual(bottom[key],top[key])

    def test_invalid_outline_never_invents_four_bounding_box_sides(self):
        self.assertEqual(simplified_outer_sides(((0,0),(0,0))), ())
        self.assertEqual(simplified_outer_sides(((0,0),(1,float("nan")),(2,0))), ())


class ForcedSearchTests(unittest.TestCase):
    def test_engine_respects_forced_first_operation_and_reverse(self):
        rect=((0.,0.),(100.,0.),(100.,100.),(0.,100.))
        state,ops=sample_part(rect)
        target=ops[1].operation_id
        result=search_fast_candidates(
            state,ops,screen_tool_center_axis=False,
            forced_first_operation_ids=(target,),
            forced_first_reverse={target:True},
            limit_seconds=1.0,beam_width=24,max_candidates=4,
        )
        self.assertTrue(result.candidates)
        for cand in result.candidates:
            self.assertEqual(cand.operation_ids[0],target)
            self.assertTrue(cand.orientations()[target])

    def test_feedback_soft_penalty_is_counted_not_rejected(self):
        rect=((0.,0.),(100.,0.),(100.,100.),(0.,100.))
        state,ops=sample_part(rect)
        target=ops[1].operation_id
        result=search_fast_candidates(
            state,ops,screen_tool_center_axis=False,
            forced_first_operation_ids=(target,),
            forced_first_reverse={target:True},
            transition_penalties={(0,target,True):1500.0},
            limit_seconds=1.0,beam_width=24,max_candidates=4,
        )
        self.assertTrue(result.candidates)
        self.assertGreaterEqual(result.candidates[0].feedback_penalty,1500.)
        self.assertEqual(result.candidates[0].operation_ids[0],target)

    def test_manual_lock_wins_over_side_requested_orientation(self):
        square=((0.,0.),(100.,0.),(100.,100.),(0.,100.))
        state,ops=sample_part(square)
        target=ops[0].operation_id
        result=search_fast_candidates(
            state,ops,screen_tool_center_axis=False,
            fixed_orientations={target:False},
            forced_first_operation_ids=(target,),
            forced_first_reverse={target:True},
            limit_seconds=1.0,beam_width=8,max_candidates=2,
        )
        self.assertFalse(result.candidates)

    def test_recovery_explicitly_visits_every_square_and_hexagonal_side(self):
        for n in (4,6):
            with self.subTest(n=n):
                outline=tuple(
                    (100*cos(2*pi*i/n),100*sin(2*pi*i/n))
                    for i in range(n)
                )
                state,ops=sample_part(outline)
                seen=[]
                def fake_search(state,steps,**kwargs):
                    starts=kwargs["forced_first_operation_ids"]
                    first=starts[0]
                    seen.append((
                        tuple(starts),
                        dict(kwargs["forced_first_reverse"]),
                    ))
                    order=(first,)+tuple(
                        step.operation_id for step in steps
                        if step.operation_id!=first
                    )
                    first_reverses=kwargs["forced_first_reverse"]
                    candidate=FastCandidate(
                        order,
                        tuple((oid,bool(first_reverses.get(oid,False)))
                              for oid in order),
                        2.0,0.0,
                    )
                    return SimpleNamespace(
                        candidates=(candidate,),
                        status="CANDIDATES_UNVERIFIED",
                        explored_states=1,elapsed_seconds=0.001,
                    )
                report=search_all_outer_sides(
                    state,ops,(),outline,{},budget_seconds=2.5,
                    search_fn=fake_search,
                )
                self.assertEqual(len(seen),n)
                self.assertEqual(len(report.side_findings),n)
                self.assertEqual(len(report.new_candidates),n)
                self.assertTrue(all(
                    entry["new_candidates"]==1
                    for entry in report.side_findings
                ))

    def test_feedback_recovers_opposite_orientation_and_new_order(self):
        rectangle = ((0.,0.),(100.,0.),(100.,100.),(0.,100.))
        state, ops = sample_part(rectangle)
        first, blocked = ops[0].operation_id, ops[1].operation_id
        original = FastCandidate(
            tuple(op.operation_id for op in ops),
            tuple((op.operation_id, False) for op in ops),
            20.0, 0.0,
        )
        issue = ShortlistCandidateCheck(
            original.operation_ids, "POTENTIAL_2D_INTERFERENCE",
            2, 0, blocked + ":punch_overlap", 0.1,
            (1, blocked, False),
        )
        screen = SimpleNamespace(
            checks=(issue,), ranked_indices=(0,),
        )
        observed = []

        def fake_search(state, steps, **kwargs):
            observed.append(kwargs)
            self.assertEqual(kwargs["forced_first_operation_ids"], (first,))
            self.assertTrue(kwargs["fixed_orientations"][blocked])
            self.assertEqual(kwargs["transition_penalties"][(1, blocked, False)],
                             1500.0)
            rotated = FastCandidate(
                original.operation_ids,
                tuple((oid, oid == blocked) for oid in original.operation_ids),
                25.0, 0.0,
            )
            return SimpleNamespace(
                candidates=(rotated,),
                status="CANDIDATES_UNVERIFIED",
                elapsed_seconds=0.01,
            )

        result = search_feedback_recovery(
            state, ops, (original,), {}, screen,
            budget_seconds=1.5, search_fn=fake_search,
        )
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(result.new_candidates), 1)
        self.assertTrue(
            result.new_candidates[0].candidate.orientations()[blocked]
        )
        self.assertEqual(result.attempted_cases[0]["flipped_operation"], blocked)
        self.assertEqual(result.status, "FEEDBACK_NEW_CANDIDATES_UNVERIFIED")

    def test_feedback_respects_manual_pinned_reverse(self):
        square = ((0.,0.),(100.,0.),(100.,100.),(0.,100.))
        state, ops = sample_part(square)
        ids = tuple(op.operation_id for op in ops)
        original = FastCandidate(
            ids, tuple((op, False) for op in ids), 10.0, 0.0,
        )
        blocked = ids[1]
        issue = ShortlistCandidateCheck(
            ids, "POTENTIAL_2D_INTERFERENCE",
            2, 0, "blocked", 0.1, (1, blocked, False),
        )
        result = search_feedback_recovery(
            state, ops, (original,), {},
            SimpleNamespace(checks=(issue,), ranked_indices=(0,)),
            fixed_orientations={blocked: False},
            search_fn=lambda *args, **kwargs: self.fail(
                "Never override explicit user orientation"
            ),
        )
        self.assertFalse(result.new_candidates)
        self.assertEqual(result.status, "NO_UNPINNED_FLIP_CASES")

    def test_suspect_2d_feedback_uses_exact_mask_and_orientation(self):
        report=SimpleNamespace(checks=(
            ShortlistCandidateCheck(
                ("B01","B02"),"POTENTIAL_2D_INTERFERENCE",
                2,0,"B02:flange",0.1,(1,"B02",True),
            ),
            ShortlistCandidateCheck(
                ("B02","B01"),"SAMPLED_2D_TOOLS_ONLY",
                2,0,"",0.1,None,
            ),
        ))
        costs=transition_feedback_penalties(report)
        self.assertEqual(costs,{(1,"B02",True):1500.})
        self.assertNotIn((1,"B02",False),costs)


if __name__=="__main__":
    unittest.main()
