"""Bounded FAST/Stage B replay on isolated fold state and one selected tool pair."""
from dataclasses import dataclass
from .fast_bfm_beam_search import search_fast_candidates
from .fast_shortlist_screening import screen_fast_shortlist
from .panel_kinematics import FoldState


@dataclass(frozen=True)
class PairFastReview:
    status: str
    candidate: object = None
    fast_report: object = None
    screen_report: object = None

    def rank(self):
        order = {
            "SAMPLED_2D_TOOLS_GAUGE_CLEAR": 0, "SAMPLED_2D_TOOLS_ONLY": 1,
            "PARTIAL_UNVERIFIED": 2, "STAGED_UNSUPPORTED": 3,
            "BUDGET_EXCEEDED": 3, "NOT_CHECKED": 3,
            "POTENTIAL_2D_INTERFERENCE": 4, "STATIC_TOOLING_BLOCK": 5,
            "NO_FAST_CANDIDATE": 6, "ESTIMATED_LIMIT_EXCEEDED": 7, "ERROR": 7,
        }
        return (order.get(self.status, 3),
                getattr(self.candidate, "estimated_cost", float("inf")))


def review_pair(fold_state, operations, outlines, tooling, *, pinned, weights,
                backgauge_model, fast_s=4.0, screen_s=2.0,
                trace=lambda *a, **k: None):
    # Never temporarily fold the live simulation's geometry.
    state = FoldState(fold_state.graph, fold_state.fixed_panel)
    trace("FAST_STARTED", operation_ids=[o.operation_id for o in operations],
          pinned=pinned, weights=weights, budget_s=fast_s)
    report = search_fast_candidates(
        state, operations, fixed_orientations=pinned, panel_vertices_xy=outlines,
        weights=weights, screen_tool_center_axis=True, axis_clearance_mm=1.0,
        tooling_setup=tooling, backgauge_model=backgauge_model,
        limit_seconds=fast_s, beam_width=32, max_candidates=3)
    trace("FAST_RESULT", report=report.to_dict())
    if not report.candidates:
        return PairFastReview("NO_FAST_CANDIDATE", fast_report=report)
    trace("2D_STARTED", budget_s=screen_s, candidate_count=len(report.candidates))
    screen = screen_fast_shortlist(
        state, operations, report.candidates, outlines, tooling,
        backgauge_model=backgauge_model, budget_seconds=screen_s)
    trace("2D_RESULT", report=screen.to_dict())
    best = screen.ranked_indices[0]
    return PairFastReview(screen.checks[best].status, report.candidates[best], report, screen)
