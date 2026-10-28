"""
test_panel_face_resolver_bridge_caps.py - Unit tests for bridge-cap face classification and diagnostics (P0).

Covers:
1. test_fixture_l_bridge_caps_detected: faces 1 and 4 are detected as BRIDGE_CAP spanning bend B1.
2. test_fixture_l_diagnostics_stored_on_graph: panel_graph stores unassigned_faces, bridge_caps, and face_diagnostics.
3. test_box_fixture_zero_bridge_caps: native SheetMetal box has zero unassigned faces and zero bridge-caps.
4. test_replayer_warning_includes_bridge_cap_details: replayer warning contains bridge-cap diagnostic info.
5. test_all_panels_strictly_disjoint: panel face_indices remain strictly disjoint.
"""

import unittest
from pathlib import Path

from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
from tests.test_kl12_v2_side_edges import _setup_l_fixture, _setup_box_fixture


class TestBridgeCapClassification(unittest.TestCase):
    def setUp(self):
        self.backend = PythonOCCBackend()

    def test_fixture_l_bridge_caps_detected(self):
        """Faces 1 and 4 on Fixture L are classified as BRIDGE_CAP spanning bend B1."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)

        self.assertTrue(hasattr(pg, "bridge_caps"), "panel_graph must have bridge_caps attribute")
        self.assertEqual(pg.bridge_caps, [1, 4])
        self.assertEqual(pg.unassigned_faces, [1, 4])

        for cap_idx in [1, 4]:
            self.assertIn(cap_idx, pg.face_diagnostics)
            diag = pg.face_diagnostics[cap_idx]
            self.assertEqual(diag["classification"], "BRIDGE_CAP")
            self.assertEqual(diag["spanning_bends"], ["B1"])
            self.assertEqual(sorted(diag["adjacent_panels"]), ["P1", "P2"])
            self.assertIn("cannot be rigidly assigned", diag["reason"])

    def test_box_fixture_zero_bridge_caps(self):
        """Native FreeCAD box fixture with corner reliefs has 0 unassigned faces and 0 bridge caps."""
        shape, backend, pg, payload = _setup_box_fixture()
        resolve_panel_faces(shape, pg, payload)

        self.assertEqual(pg.bridge_caps, [])
        self.assertEqual(pg.unassigned_faces, [])
        self.assertEqual(len(pg.face_diagnostics), 0)

    def test_all_panels_strictly_disjoint(self):
        """Assigned panels have strictly disjoint face_indices with zero duplicates."""
        shape, backend, pg, payload = _setup_l_fixture()
        resolve_panel_faces(shape, pg, payload)

        all_indices = []
        for p in pg.panels.values():
            all_indices.extend(p.face_indices)
        self.assertEqual(len(all_indices), len(set(all_indices)), "Panels share duplicate face_indices")

        # Bridge caps must not be assigned to any panel
        for cap in pg.bridge_caps:
            self.assertNotIn(cap, all_indices)

    def test_replayer_warning_includes_bridge_cap_details(self):
        """Replayer warning contains informative bridge-cap diagnostic for Fixture L."""
        from smbs_core.viewer.replayer import replay_backward
        from smbs_core.viewer.graph_builder import build_graphs
        from smbs_core.viewer.run_loader import RunArtifacts

        shape, backend, pg, payload = _setup_l_fixture()
        payload["bends"]["B1"] = {
            "parent_panel_id": "P1",
            "child_panel_id": "P2",
            "axis_point": [95.5, 0.0, 2.5],
            "axis_direction": [0.0, 1.0, 0.0],
            "angle_deg": {"value": 90.0},
            "radius_mm": {"value": 1.0},
            "length_mm": {"value": 50.0},
            "direction": {"value": "UP"},
        }
        artifacts = RunArtifacts(
            run_dir=Path("."),
            canonical_part={"schema_name": "canonical_part", "schema_version": "1.0", "payload": payload},
            search_result={
                "schema_name": "search_result",
                "schema_version": "1.1",
                "payload": {
                    "candidate_sequence": [
                        {
                            "step": 1,
                            "bend_id": "B1",
                            "planner_status": "VALID",
                            "collision_status": "VALID",
                            "tooling_status": "SIMPLIFIED",
                            "alignment_status": "OK",
                        }
                    ]
                },
            },
            verification_result=None,
            geometry_shape_path=Path("dummy_geometry.brep"),
            flat_pattern_path=None,  # triggers kinematic rotate_subtree path
        )
        graphs = build_graphs(artifacts)
        views = list(replay_backward(artifacts, graphs, backend, initial_shape=shape))

        self.assertGreater(len(views), 0)
        warning = views[0].geometry_warning
        self.assertIsNotNone(warning)
        self.assertIn("bridge-cap(s) spanning bends", warning)
        self.assertIn("2 unassigned face(s)", warning)


if __name__ == "__main__":
    unittest.main()
