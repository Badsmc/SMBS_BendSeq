"""A single short-rear orientation map drives Manual preview and OCC."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smbs_core.kinematics2d.loading_orientation_preview import (
    plan_preview_loading_orientations,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.press_cycle import CycleConfig
from smbs_core.kinematics2d.tooling_25d import ToolingSetup2D
from freecad_smbs.integration.fcstd_session_store import build_snapshot


GUI = Path(__file__).resolve().parents[1] / "freecad_smbs/commands/pressbrake_2d.py"


def _method(name):
    parsed = ast.parse(GUI.read_text(encoding="utf-8"))
    methods = [node for node in ast.walk(parsed)
               if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(methods) != 1:
        raise AssertionError(name)
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=methods, type_ignores=[],
    )), str(GUI), "exec"), namespace)
    return namespace[name]


def _fixture():
    graph = PanelGraph(("P01", "P02"), (
        GraphEdge("B01", "P01", "P02", 90,
                  ((80, 0, 0), (80, 50, 0))),
    ))
    state = FoldState(graph, "P01")
    vertices = {
        "P01": ((0, 0), (80, 0), (80, 50), (0, 50)),
        "P02": ((80, 0), (100, 0), (100, 50), (80, 50)),
    }
    return state, vertices


class ShortRearOrientationTests(unittest.TestCase):
    def test_short_flange_faces_rear_without_a_gauge_model(self):
        state, vertices = _fixture()
        result = plan_preview_loading_orientations(
            state, vertices, ("B01",), deadband_mm=0.0,
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].reverse_axis)
        self.assertEqual(result[0].rear_reach_mm, 20.0)
        self.assertEqual(result[0].operator_reach_mm, 80.0)
        self.assertEqual(state.angles["B01"], 0.0)

    def test_manual_map_matches_viewer_map_with_no_checkbox(self):
        state, vertices = _fixture()
        effective = _method("_effective_loading_orientation_map")
        select = _method("_manual_orientation_selection")
        workspace = SimpleNamespace(
            session=SimpleNamespace(bend_order=("B01",), fold_state=state,
                                    panel_vertices_xy=vertices),
            _manual_orientation_order=(), _manual_orientation_by_bend={},
            _ergonomic_loading_cache_key=None, _ergonomic_loading_cache={},
            backgauge_model=None,
        )
        workspace._effective_loading_orientation_map = lambda: effective(workspace)
        self.assertEqual(effective(workspace), {"B01": True})
        self.assertEqual(select(workspace, ("B01",)), {"B01": True})
        self.assertEqual(workspace._ergonomic_loading_status,
                         "AUTO_SHORT_REAR_UNVERIFIED")

    def test_selected_2d_candidate_remains_authoritative(self):
        state, vertices = _fixture()
        workspace = SimpleNamespace(
            session=SimpleNamespace(bend_order=("B01",), fold_state=state,
                                    panel_vertices_xy=vertices),
            _manual_orientation_order=("B01",),
            _manual_orientation_by_bend={"B01": False},
        )
        self.assertEqual(_method("_effective_loading_orientation_map")(
            workspace), {"B01": False})
        self.assertEqual(workspace._ergonomic_loading_status,
                         "SELECTED_2D_ORIENTATION_UNVERIFIED")

    def test_removed_checkbox_and_shared_native_map(self):
        source = GUI.read_text(encoding="utf-8")
        self.assertNotIn("manual_reverse_3d_checkbox", source)
        self.assertIn("reverse_selection = self._manual_orientation_selection(selected)", source)
        self.assertEqual(source.count("reverse_axis_by_bend=reverse_selection"), 2)
        self.assertIn("self._effective_loading_orientation_map()", source)
        trajectory = ast.get_source_segment(
            source, next(n for n in ast.walk(ast.parse(source))
                         if isinstance(n, ast.FunctionDef)
                         and n.name == "check_active_bend_physical_trajectory"),
        )
        self.assertEqual(trajectory.count("reverse_axis=active_reverse"), 2)
        self.assertIn("return None", ast.get_source_segment(
            source, next(n for n in ast.walk(ast.parse(source))
                         if isinstance(n, ast.FunctionDef)
                         and n.name == "_rear_frame_for_preview"),
        ))

    def test_fcstd_snapshot_labels_orientation_provenance(self):
        state, vertices = _fixture()
        session = SimpleNamespace(bend_order=("B01",), canonical_order=("B01",))
        with patch("freecad_smbs.integration.fcstd_session_store.geometry_manifest",
                   return_value={"sha256": "f" * 64}):
            snapshot = build_snapshot(
                session, SimpleNamespace(), ToolingSetup2D(), CycleConfig(),
                "FULL_CYCLE", orientation_by_bend={"B01": True},
                orientation_source="AUTO_SHORT_REAR",
            )
        self.assertEqual(snapshot["order"]["orientation_by_bend"], {"B01": True})
        self.assertEqual(snapshot["order"]["orientation_source"], "AUTO_SHORT_REAR")


if __name__ == "__main__":
    unittest.main()
