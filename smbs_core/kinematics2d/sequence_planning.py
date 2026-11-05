"""D6 GUI-to-core planning entry point (headless, no FreeCAD/Qt calls).

D6's physical backend is deliberately fail-closed: this 2.5D adapter tests
sampled cross sections against verified imported punch/die profiles. It can
find a diagnostic candidate but cannot issue a full machine-certified order
until the existing B-Rep PhysicalValidator has compatible solid state and
machine/tooling geometry for all required collision scopes.
"""
from __future__ import annotations
from dataclasses import dataclass

from .panel_kinematics import FoldState
from .physical_step_oracle_25d import CandidateCheck, PreviewStepOracle25D
from .sequence_search import SequenceSearchResult, plan_forward
from .tooling_25d import ToolingSetup2D


@dataclass(frozen=True)
class PlanningRun:
    result: SequenceSearchResult
    candidate_checks: tuple[CandidateCheck, ...]
    validation_mode: str = "2.5D_CANDIDATE_PREVIEW_UNVERIFIED_3D"


def calculate_sequence_candidates(
    state: FoldState, panel_vertices_xy: dict, tooling: ToolingSetup2D,
    *, max_states: int = 500, max_checks: int = 2000,
) -> PlanningRun:
    """Return candidates and explicit blockers without modifying live playback."""
    adapter = PreviewStepOracle25D(panel_vertices_xy, tooling)
    result = plan_forward(
        state, adapter, max_states=max_states, max_checks=max_checks,
        allow_partial_preview=True,
    )
    return PlanningRun(result, tuple(adapter.history))
