"""Regression for selected real punch/die lengths in manual Stage A."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.preliminary_screening import (
    ScreeningDecision, screen_candidate_stage_a,
)
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D


def fixture(punch_length=100, die_length=100):
    graph = PanelGraph(("P01", "P02"), (
        GraphEdge("B01", "P01", "P02", 90.0,
                  ((10, 0, 0), (10, 50, 0))),
    ))
    state = FoldState(graph, "P01")
    tooling = ToolingSetup2D()
    tooling.punch = SimpleNamespace(length_mm=punch_length)
    tooling.die = SimpleNamespace(length_mm=die_length)
    return state, tooling


class SelectedToolStageATests(unittest.TestCase):
    def test_real_punch_shorter_than_50mm_bend_is_rejected(self):
        state, tools = fixture(punch_length=35)
        report = screen_candidate_stage_a(
            state, "B01", tools, match_active_bend_length=False,
        )
        self.assertEqual(report.decision, ScreeningDecision.PROVEN_BLOCKING)
        self.assertIn("selected_punch_shorter_than_bend", report.reason)

    def test_real_die_shorter_than_50mm_bend_is_rejected(self):
        state, tools = fixture(die_length=45)
        report = screen_candidate_stage_a(
            state, "B01", tools, match_active_bend_length=False,
        )
        self.assertEqual(report.decision, ScreeningDecision.PROVEN_BLOCKING)
        self.assertIn("selected_die_shorter_than_bend", report.reason)

    def test_explicit_virtual_length_allows_longer_bend_for_later_3d(self):
        state, tools = fixture(punch_length=20, die_length=20)
        report = screen_candidate_stage_a(
            state, "B01", tools, match_active_bend_length=True,
        )
        self.assertEqual(report.decision, ScreeningDecision.CONTINUE_TO_3D)

    def test_missing_punch_not_silently_accepted(self):
        state, tools = fixture()
        tools.punch = None
        report = screen_candidate_stage_a(state, "B01", tools)
        self.assertEqual(report.decision, ScreeningDecision.PROVEN_BLOCKING)
        self.assertIn("selected_punch_and_die_required", report.reason)

    def test_assembled_real_sections_deferred_to_native_span_check(self):
        state, tools = fixture(punch_length=10, die_length=10)
        tools.punch_section_offsets_xm = (-25, 25)
        tools.die_section_offsets_xm = (-25, 25)
        report = screen_candidate_stage_a(
            state, "B01", tools, match_active_bend_length=False,
        )
        self.assertEqual(report.decision, ScreeningDecision.CONTINUE_TO_3D)


if __name__ == "__main__":
    unittest.main()
