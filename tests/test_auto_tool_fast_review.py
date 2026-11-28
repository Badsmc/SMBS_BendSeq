"""Regression tests for auto-tool FAST review and diagnostic event ordering."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from freecad.bendseq.core.kinematics2d.auto_tool_fast_review import review_pair, PairFastReview

MODULE = "freecad.bendseq.core.kinematics2d.auto_tool_fast_review"


class AutoToolFastReviewTests(unittest.TestCase):
    def test_reuses_fast_and_stage_b_without_live_fold_state(self):
        live = NS(graph=object(), fixed_panel="P0")
        state = object()
        operations = (NS(operation_id="B01"),)
        candidate_a = NS(estimated_cost=2)
        candidate_b = NS(estimated_cost=5)
        report = Mock(candidates=(candidate_a, candidate_b))
        screen = Mock(ranked_indices=(1, 0), checks=(
            NS(status="POTENTIAL_2D_INTERFERENCE"),
            NS(status="SAMPLED_2D_TOOLS_ONLY")))
        events = []
        def trace(name, **data):
            events.append(name)
        with patch(MODULE + ".FoldState", return_value=state) as clone, \
             patch(MODULE + ".search_fast_candidates", return_value=report) as fast, \
             patch(MODULE + ".screen_fast_shortlist", return_value=screen) as check:
            result = review_pair(live, operations, {}, object(), pinned={"B01": True},
                                 weights={}, backgauge_model=None, trace=trace)
        clone.assert_called_once_with(live.graph, "P0")
        self.assertIs(fast.call_args.args[0], state)
        self.assertIs(check.call_args.args[0], state)
        self.assertEqual(fast.call_args.kwargs["fixed_orientations"], {"B01": True})
        self.assertIs(result.candidate, candidate_b)
        self.assertEqual(events, ["FAST_STARTED", "FAST_RESULT", "2D_STARTED", "2D_RESULT"])

    def test_empty_fast_report_is_not_clearance(self):
        live = NS(graph=object(), fixed_panel="P0")
        with patch(MODULE + ".FoldState"), \
             patch(MODULE + ".search_fast_candidates", return_value=Mock(candidates=())), \
             patch(MODULE + ".screen_fast_shortlist") as check:
            result = review_pair(live, (), {}, object(), pinned={}, weights={}, backgauge_model=None)
        self.assertEqual(result.status, "NO_FAST_CANDIDATE")
        self.assertIsNone(result.candidate)
        check.assert_not_called()

    def test_sampled_clear_ranks_before_potential_interference(self):
        candidate = NS(estimated_cost=10)
        self.assertLess(PairFastReview("SAMPLED_2D_TOOLS_ONLY", candidate).rank(),
                        PairFastReview("POTENTIAL_2D_INTERFERENCE", NS(estimated_cost=1)).rank())
        self.assertLess(PairFastReview("NOT_CHECKED").rank(), PairFastReview("ERROR").rank())
