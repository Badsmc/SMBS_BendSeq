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

    def test_missing_face_geometry_fails_closed(self):
        session = self.make_session()
        session.panel_build.panel_faces = ()
        report = search_reverse_aabb(session, budget_s=1)
        self.assertFalse(report.geometry_available)
        self.assertFalse(report.candidates)


if __name__ == "__main__":
    unittest.main()
