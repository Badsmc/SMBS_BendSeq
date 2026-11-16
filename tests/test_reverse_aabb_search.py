"""Pure Python tests for the experimental reverse AABB candidate generator."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.reverse_aabb_search import search_reverse_aabb


def face(coords):
    vertices = [SimpleNamespace(Point=SimpleNamespace(x=x, y=y))
                for x, y in coords]
    return SimpleNamespace(
        OuterWire=SimpleNamespace(OrderedVertexes=vertices),
        Vertexes=vertices,
    )


class ReverseAABBTests(unittest.TestCase):
    def make_session(self):
        graph = PanelGraph(
            ("P1", "P2", "P3"),
            (
                GraphEdge("B1", "P1", "P2", 90.0,
                          ((10.0, 0.0, 0.0), (10.0, 10.0, 0.0))),
                GraphEdge("B2", "P2", "P3", 90.0,
                          ((20.0, 0.0, 0.0), (20.0, 10.0, 0.0))),
            ),
        )
        state = FoldState(graph, "P1")
        faces = (
            face(((0, 0), (10, 0), (10, 10), (0, 10))),
            face(((10, 0), (20, 0), (20, 10), (10, 10))),
            face(((20, 0), (30, 0), (30, 10), (20, 10))),
        )
        panels = tuple(SimpleNamespace(panel_id=p) for p in graph.panels)
        build = SimpleNamespace(panels=panels, panel_faces=faces)
        return SimpleNamespace(fold_state=state, panel_build=build)

    def test_full_candidate_and_no_operator_mutation(self):
        session = self.make_session()
        before = dict(session.fold_state.angles)
        report = search_reverse_aabb(session, budget_s=10,
                                     max_candidates=4)
        self.assertTrue(report.geometry_available)
        self.assertTrue(report.candidates)
        self.assertTrue(report.visited_states)
        self.assertEqual(dict(session.fold_state.angles), before)
        for candidate in report.candidates:
            self.assertEqual(set(candidate.sequence), {"B1", "B2"})
            self.assertEqual(len(candidate.sequence), 2)
            self.assertIn("UNVERIFIED", candidate.evidence)
        self.assertFalse(report.to_dict().get("machine_feasible_certified", False))

    def test_machine_envelopes_rerank_complete_orders(self):
        from unittest.mock import patch
        from smbs_core.kinematics2d.reverse_machine_screen import ToolAABB
        session = self.make_session()
        envelopes = {
            bid: ToolAABB(
                punch=(-50, -50, 35, 50, 50, 55),
                die=(-50, -50, -10, 50, 50, 0),
            )
            for bid in session.fold_state.bends
        }
        with patch(
            "smbs_core.kinematics2d.reverse_machine_screen.load_tool_boxes",
            return_value=(envelopes, "NATIVE_TOOL_ENVELOPES_LOADED"),
        ) as loaded:
            report = search_reverse_aabb(
                session, tooling=object(), budget_s=10,
                max_candidates=2,
            )
        self.assertEqual(loaded.call_count, 1)
        self.assertTrue(report.candidates)
        self.assertGreater(report.machine_screen_pairs, 0)
        self.assertGreater(report.machine_screen_candidates, 0)
        self.assertEqual(report.machine_screen_status,
                         "NATIVE_TOOL_ENVELOPES_LOADED")
        self.assertTrue(all(
            item.evidence == "REVERSE_ASTAR_MACHINE_AABB_RANKED_UNVERIFIED"
            for item in report.candidates
        ))
        self.assertEqual(
            set(dict(report.candidates[0].orientation_by_bend)),
            set(session.fold_state.bends),
        )

    def test_missing_face_geometry_fails_closed(self):
        session = self.make_session()
        session.panel_build.panel_faces = ()
        report = search_reverse_aabb(session, budget_s=1)
        self.assertFalse(report.geometry_available)
        self.assertFalse(report.candidates)


if __name__ == "__main__":
    unittest.main()
