"""FAST BFM + cached beam regression: deterministic candidates, not 3D PASS."""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.fast_bfm_beam import (
    FastProblem, FastStep, search_fast_beam,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import StagedBendSequenceController


def session_with_bends(count=10):
    panels = tuple("P{:02d}".format(i) for i in range(count + 1))
    edges = tuple(
        GraphEdge(
            "B{:02d}".format(i), panels[i - 1], panels[i], 90.0,
            ((0.0, float(i) * 10.0, 0.0),
             (100.0 if i % 2 else 200.0, float(i) * 10.0, 0.0)),
        ) for i in range(1, count + 1)
    )
    fs = FoldState(PanelGraph(panels, edges), panels[0])
    return SimpleNamespace(fold_state=fs, controller=StagedBendSequenceController(fs))


class FastBeamSearchTests(unittest.TestCase):
    def test_ten_bends_find_candidates_under_budget(self):
        session = session_with_bends(10)
        original_angles = dict(session.fold_state.angles)
        p = FastProblem.from_session(session)
        a = search_fast_beam(p, beam_width=24, max_candidates=5, time_budget_s=1.0)
        self.assertEqual(a.status, "CANDIDATES_UNVERIFIED")
        self.assertTrue(1 <= len(a.candidates) <= 5)
        self.assertLess(a.elapsed_s, 1.0)
        self.assertEqual(a.candidates[0].operation_ids, tuple(session.controller.order))
        self.assertEqual(session.fold_state.angles, original_angles)
        self.assertTrue(all(c.to_dict()["evidence"] == "UNVERIFIED_FAST_TOPOLOGY"
                            for c in a.candidates))

    def test_both_stages_require_prebend_before_finish(self):
        session = session_with_bends(4)
        c = session.controller
        c.add_prebend("B02", 30.0)
        c.add_prebend("B04", 45.0)
        p = FastProblem.from_session(
            session, forced_orientations={"B02.1": True, "B02.2": False}
        )
        r = search_fast_beam(p)
        self.assertTrue(r.candidates)
        for candidate in r.candidates:
            ops = candidate.operation_ids
            self.assertLess(ops.index("B02.1"), ops.index("B02.2"))
            self.assertLess(ops.index("B04.1"), ops.index("B04.2"))
            self.assertTrue(candidate.orientation_map["B02.1"])
            self.assertFalse(candidate.orientation_map["B02.2"])
            self.assertEqual(len(ops), 6)

    def test_conservative_matrix_enforces_only_provided_physical_rules(self):
        session = session_with_bends(3)
        p = FastProblem.from_session(
            session,
            verified_precedences=(("B03", "B02"),),
            verified_forbidden_transitions=(("B03", "B01"),),
        )
        r = search_fast_beam(p, beam_width=40)
        self.assertTrue(r.candidates)
        self.assertGreater(r.matrix_rejected, 0)
        for c in r.candidates:
            a = c.operation_ids
            self.assertLess(a.index("B03"), a.index("B02"))
            self.assertNotIn(("B03", "B01"), tuple(zip(a, a[1:])))

    def test_screen_callback_blocks_when_grounded_and_uses_cache(self):
        session = session_with_bends(4)
        p = FastProblem.from_session(session)
        calls = []
        def check(mask, op, rev):
            calls.append((mask, op.operation_id, rev))
            if op.operation_id == "B03":
                return "BLOCK"
            return "UNKNOWN"
        r = search_fast_beam(p, beam_width=48, quick_screen=check)
        self.assertEqual(r.status, "NO_CANDIDATES_UNVERIFIED")
        self.assertGreater(r.screen_rejected, 0)
        self.assertEqual(len(set(calls)), len(calls))

    def test_budget_exhaustion_is_not_false_infeasibility(self):
        session = session_with_bends(15)
        p = FastProblem.from_session(session)
        tick = [0.0]
        def clock():
            tick[0] += 0.02
            return tick[0]
        r = search_fast_beam(p, clock=clock, time_budget_s=0.05)
        self.assertEqual(r.status, "BUDGET_EXHAUSTED")
        self.assertFalse(r.candidates)
        self.assertIn("UNVERIFIED", " ".join(r.warnings))

    def test_problem_rejects_untrusted_unknown_operation(self):
        session = session_with_bends(3)
        with self.assertRaisesRegex(ValueError, "unknown operation"):
            FastProblem.from_session(session, forced_orientations={"B99": True})
        with self.assertRaisesRegex(ValueError, "invalid feasibility matrix"):
            FastProblem.from_session(
                session, verified_precedences=(("B99", "B01"),)
            )

    def test_two_pass_staging_and_180_choices_remain_independent(self):
        session = session_with_bends(3)
        c = session.controller
        c.add_prebend("B01", 25.0)
        p = FastProblem.from_session(
            session, forced_orientations={"B01.1": False, "B01.2": True}
        )
        r = search_fast_beam(p, beam_width=64)
        self.assertTrue(r.candidates)
        self.assertTrue(all(
            candidate.orientation_map["B01.1"] is False
            and candidate.orientation_map["B01.2"] is True
            for candidate in r.candidates
        ))


if __name__ == "__main__":
    unittest.main()
