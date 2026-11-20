"""FAST BFM-lite candidate generator: safety, staged state and bounded work."""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin
from time import perf_counter
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.fast_bfm_beam_search import (
    FastSearchError, _segment_distance, search_fast_candidates,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import (
    BendOperation, StagedBendSequenceController,
)


def sample_state(count=5):
    edges = []
    for k in range(count):
        # Orthogonal axes, disjoint and fully finite in XY.
        x = 20*(k//2)
        y = 20*k
        axis = ((x,y,0.0),(x+(40 if k%2 else 0),y+(0 if k%2 else 40),0.0))
        edges.append(GraphEdge(
            f"B{k+1:02d}", f"P{k}", f"P{k+1}", 90.0,
            axis,
        ))
    return FoldState(PanelGraph(
        tuple(f"P{k}" for k in range(count+1)),tuple(edges)
    ),"P0")


class FastCandidateTests(unittest.TestCase):
    def test_returns_only_unverified_candidates_with_unique_operations(self):
        state = sample_state(8)
        before = dict(state.angles)
        report = search_fast_candidates(state,beam_width=24)
        self.assertEqual(report.status,"CANDIDATES_UNVERIFIED")
        self.assertTrue(1<=len(report.candidates)<=5)
        self.assertEqual(before,state.angles)
        target = set(state.bends)
        for c in report.candidates:
            self.assertEqual(set(c.operation_ids),target)
            self.assertEqual(len(c.operation_ids),8)
            self.assertEqual(c.evidence,"UNVERIFIED_3D_NOT_CHECKED")
            self.assertEqual(set(c.orientations()),target)
        self.assertEqual(report.to_dict()["candidates"][0]["evidence"],
                         "UNVERIFIED_3D_NOT_CHECKED")

    def test_staged_prebend_dependency_even_if_original_order_wrong(self):
        state = sample_state(5)
        c = StagedBendSequenceController(state)
        c.add_prebend("B01",25)
        c.add_prebend("B04",42)
        c.reorder_operations(
            ("B01.1","B02","B03","B04.1","B01.2","B05","B04.2")
        )
        report = search_fast_candidates(state,c.operation_steps,
                                        fixed_orientations={"B04.1":True},
                                        beam_width=64)
        self.assertTrue(report.candidates)
        for candidate in report.candidates:
            order=candidate.operation_ids
            self.assertLess(order.index("B01.1"),order.index("B01.2"))
            self.assertLess(order.index("B04.1"),order.index("B04.2"))
            self.assertEqual(candidate.orientations()["B04.1"],True)
            self.assertEqual(len(order),7)
        self.assertEqual(c.operation_snapshot(),c.operation_snapshot())

    def test_orientation_choices_locked_independently_per_stage(self):
        state=sample_state(3)
        controller=StagedBendSequenceController(state)
        controller.add_prebend("B02",35)
        pins={"B02.1":True,"B02.2":False}
        report=search_fast_candidates(state,controller.operation_steps,
                                      fixed_orientations=pins)
        self.assertTrue(report.candidates)
        for c in report.candidates:
            self.assertTrue(c.orientations()["B02.1"])
            self.assertFalse(c.orientations()["B02.2"])

    def test_deterministic_same_inputs(self):
        state=sample_state(6)
        a=search_fast_candidates(state,beam_width=32)
        b=search_fast_candidates(state,beam_width=32)
        self.assertEqual([c.to_dict() for c in a.candidates],
                         [c.to_dict() for c in b.candidates])

    def test_incomplete_or_invalid_staging_rejected(self):
        s=sample_state(2)
        invalid=(
            (BendOperation("B01.2","B01",90,"FINISH"),),
            (BendOperation("B01.1","B01",99,"PREBEND"),
             BendOperation("B01.2","B01",90,"FINISH")),
            (BendOperation("B01","B01",90),BendOperation("B01","B01",90)),
        )
        for ops in invalid:
            with self.subTest(ops=ops):
                with self.assertRaises(FastSearchError):
                    search_fast_candidates(s,ops)

    def test_unknown_orientation_key_fails_not_ignored(self):
        with self.assertRaises(FastSearchError):
            search_fast_candidates(sample_state(2),
                                   fixed_orientations={"B99":True})
        with self.assertRaises(FastSearchError):
            search_fast_candidates(sample_state(2),
                                   fixed_orientations={"B01":180})

    def test_time_budget_is_checked_in_matrix_and_beam(self):
        class ControlledClock:
            def __init__(self, increment):
                self.t=0.0
                self.increment=increment
            def __call__(self):
                self.t+=self.increment
                return self.t
        report=search_fast_candidates(sample_state(15),
                                      clock=ControlledClock(0.35),
                                      limit_seconds=1.0)
        self.assertEqual(report.status,"TIME_BUDGET_UNVERIFIED")
        self.assertFalse(report.candidates)

    def test_parallel_disjoint_collinear_segments_have_real_distance(self):
        a=(0.0,0.0,10.0,0.0)
        b=(30.0,0.0,40.0,0.0)
        self.assertAlmostEqual(_segment_distance(a,b),20.0)

    def test_short_rear_flat_contour_preference_not_arbitrary(self):
        state = sample_state(1)
        # B01 is along +Y: flat machine +s points toward +X.
        # The wide +X flange should face the operator, not the rear gauge.
        outline = {
            panel: ((-10.0, 0.0), (90.0, 0.0),
                    (90.0, 45.0), (-10.0, 45.0))
            for panel in state.graph.panels
        }
        report = search_fast_candidates(
            state, panel_vertices_xy=outline,
        )
        self.assertTrue(report.candidates)
        self.assertTrue(report.candidates[0].orientations()["B01"])
        self.assertEqual(report.candidates[0].ergonomic_penalty, 0.0)
        # An operator-pinned orientation must be honored even when the
        # cheap geometric preference points the other way.
        locked = search_fast_candidates(
            state, panel_vertices_xy=outline,
            fixed_orientations={"B01": False},
        )
        self.assertFalse(locked.candidates[0].orientations()["B01"])
        self.assertGreater(locked.candidates[0].ergonomic_penalty, 0)

    def test_missing_panel_outline_does_not_invent_ergonomic_evidence(self):
        state = sample_state(1)
        report = search_fast_candidates(
            state, panel_vertices_xy={"P0": ((-10, 0), (100, 0), (100, 40))}
        )
        self.assertTrue(report.candidates)
        self.assertTrue(all(c.evidence.startswith("UNVERIFIED")
                            for c in report.candidates))

    def test_leaf_first_is_soft_priority_not_a_hard_dependency(self):
        state = sample_state(4)
        zero_weights = {
            "turn_fixed": 0.0,
            "turn_per_90_deg": 0.0,
            "length_change_fixed": 0.0,
            "length_delta_per_100_mm": 0.0,
        }
        report = search_fast_candidates(state, weights=zero_weights,
                                        beam_width=64)
        self.assertTrue(report.candidates)
        self.assertEqual(report.candidates[0].operation_ids[0], "B04")
        self.assertEqual(report.candidates[0].nested_fold_penalty, 0.0)
        self.assertTrue(any(
            c.operation_ids[0] != "B04" for c in report.candidates
        ))

    def test_five_results_are_distinct_orderings_not_only_orientation_variants(self):
        report = search_fast_candidates(sample_state(8), beam_width=64)
        orders = [c.operation_ids for c in report.candidates]
        self.assertEqual(len(orders), len(set(orders)))
        self.assertGreaterEqual(len(set(path[0] for path in orders)), 2)

    def test_15_bend_synthetic_stays_bounded_and_does_not_certify(self):
        state=sample_state(15)
        report=search_fast_candidates(state,limit_seconds=1.0,beam_width=24)
        self.assertTrue(report.elapsed_seconds>=0)
        self.assertLess(report.elapsed_seconds,1.5)
        self.assertIn(report.status,("CANDIDATES_UNVERIFIED","TIME_BUDGET_UNVERIFIED"))
        self.assertTrue(all(x.evidence.startswith("UNVERIFIED") for x in report.candidates))


if __name__ == "__main__":
    unittest.main()
