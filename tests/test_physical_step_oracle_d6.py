"""D6 integrated physical-step adapter and no-false-certification regression."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.physical_step_oracle_25d import PreviewStepOracle25D
from smbs_core.kinematics2d.sequence_search import Feasibility, SearchStatus, plan_forward
from smbs_core.kinematics2d.tooling_25d import (
    ToolingSetup2D, ValidationStatus, ToolKind,
)
from smbs_core.kinematics2d.d5_contact_validation import D51Status


def fixture():
    params = bend_parameters(90.0, 1.0, 1.5, 0.31)
    zone = make_bend_zone("B01", ((-10, 0), (10, 0)), params)
    graph = PanelGraph(("P1", "P2"), (
        GraphEdge("B01", "P1", "P2", 90.0,
                  ((-10, 0, 0), (10, 0, 0)), bend_zone=zone),
    ))
    vertices = {
        "P1": ((-10, -20), (10, -20), (10, -1.0), (-10, -1.0)),
        "P2": ((-10, 1.0), (10, 1.0), (10, 20), (-10, 20)),
    }
    return FoldState(graph, "P1"), vertices


def tool_profiles():
    class Profile:
        validation_status = ValidationStatus.GEOMETRY_VERIFIED
        working_datum = (0.0, 0.0)
        def __init__(self, kind):
            self.kind = kind
            self.name = str(kind)
            self.punch_datums = SimpleNamespace(
                tip_radius_mm=0.8, included_angle_deg=30.0,
            ) if kind is ToolKind.PUNCH else None
            self.die_datums = SimpleNamespace(
                v_opening_mm=32.0, shoulder_radius_mm=2.0,
                die_angle_deg=85.0, v_depth_mm=16.5,
            ) if kind is ToolKind.DIE else None
        def local_polygons(self, arc_segments=32):
            if self.kind is ToolKind.PUNCH:
                return ((-1, 0), (1, 0), (1, 10), (-1, 10)), ()
            return ((-20, -10), (20, -10), (20, -2), (-20, -2)), ()
    return ToolingSetup2D(
        punch=Profile(ToolKind.PUNCH), die=Profile(ToolKind.DIE),
    )


class D6IntegratedOracleTests(unittest.TestCase):
    def test_missing_tool_is_unverified(self):
        state, vertices = fixture()
        oracle = PreviewStepOracle25D(vertices, ToolingSetup2D())
        evidence = oracle(state, "B01", None)
        self.assertIs(evidence.status, Feasibility.UNVERIFIED)
        self.assertIn("missing_punch_or_die", evidence.reason)

    def test_missing_bend_zone_is_unverified_not_default(self):
        graph = PanelGraph(("P1", "P2"), (
            GraphEdge("B01", "P1", "P2", 90.0,
                      ((-10, 0, 0), (10, 0, 0))),
        ))
        state = FoldState(graph, "P1")
        _, vertices = fixture()
        evidence = PreviewStepOracle25D(vertices, tool_profiles())(state, "B01", None)
        self.assertIs(evidence.status, Feasibility.UNVERIFIED)
        self.assertIn("bend_zone", evidence.reason)

    def test_samples_machine_geometry_using_real_oracle_adapter(self):
        state, vertices = fixture()
        angles_before = dict(state.angles)
        tools = tool_profiles()
        oracle = PreviewStepOracle25D(vertices, tools)
        calls = []
        def clean(*args, **kwargs):
            calls.append(kwargs.get("phase"))
            return SimpleNamespace(status=D51Status.CLEAR, findings=())
        with patch("smbs_core.kinematics2d.physical_step_oracle_25d.check_d51_machine_section",
                   side_effect=clean):
            evidence = oracle(state, "B01", None)
        self.assertIs(evidence.status, Feasibility.CLEAR, evidence.reason)
        self.assertFalse(evidence.is_certified())
        self.assertGreaterEqual(calls.count("BENDING"), 5)
        self.assertIn("APPROACH", calls)
        self.assertIn("RETRACT", calls)
        self.assertEqual(state.angles, angles_before)
        self.assertFalse(tools.aligned)

    def test_tool_interference_blocks_candidate(self):
        state, vertices = fixture()
        oracle = PreviewStepOracle25D(vertices, tool_profiles())
        def collision(*args, **kwargs):
            return SimpleNamespace(
                status=D51Status.COLLISION,
                findings=(SimpleNamespace(
                    status=D51Status.COLLISION,
                    tool="DIE", component="P1", reason="interior_overlap",
                ),),
            )
        with patch("smbs_core.kinematics2d.physical_step_oracle_25d.check_d51_machine_section",
                   side_effect=collision):
            ev = oracle(state, "B01", None)
        self.assertIs(ev.status, Feasibility.COLLISION, ev.reason)

    def test_partial_evidence_produces_preview_not_certification(self):
        state, vertices = fixture()
        oracle = PreviewStepOracle25D(vertices, tool_profiles())
        def clean(*args, **kwargs):
            return SimpleNamespace(status=D51Status.CLEAR, findings=())
        with patch("smbs_core.kinematics2d.physical_step_oracle_25d.check_d51_machine_section",
                   side_effect=clean):
            result = plan_forward(state, oracle, allow_partial_preview=True)
        self.assertIs(result.status, SearchStatus.UNVERIFIED)
        self.assertEqual(result.sequence, ())
        self.assertEqual(result.candidate_sequence, ("B01",))
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
