"""
test_panel_face_resolver.py - Unit Tests for Runtime Panel Face Resolver (KL-12 Step 2.1)

Covers:
T1. fixture_l: 2 panels both receive face_indices.
T2. fixture_4bend: 5 panels all receive disjoint face_indices without duplicates.
T3. MagicMock shape (regression on hang): returns without modification, face_indices empty, <1s.
T4. Preserve existing: manual face_indices not overwritten.
"""

import unittest
from unittest.mock import MagicMock
import time

from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
from tests.test_technological_unfold_single_bend import create_l_bracket_shape
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar


class TestPanelFaceResolver(unittest.TestCase):
    def test_fixture_l(self):
        """T1: Resolver populates face_indices for both panels on Fixture L."""
        shape = create_l_bracket_shape(90.0, 1.0, 1.5, 50.0, 95.5, 47.5)
        pg = PanelGraph()
        pg.add_panel(Panel("P1", normal=(0.0, 0.0, 1.0)))
        pg.add_panel(Panel("P2", normal=(1.0, 0.0, 0.0)))
        pg.add_bend_connection("B1", "P1", "P2")

        canonical_payload = {
            "thickness_mm": {"value": 1.5},
            "panels": {
                "P1": {"centroid": [47.75, 25.0, 0.75], "normal": [0.0, 0.0, 1.0]},
                "P2": {"centroid": [97.25, 25.0, 26.25], "normal": [1.0, 0.0, 0.0]},
            },
            "bends": {
                "B1": {"axis_point": [95.5, 0.0, 2.5], "child_panel_id": "P2"}
            }
        }

        resolve_panel_faces(shape, pg, canonical_payload)
        self.assertTrue(all(len(p.face_indices) > 0 for p in pg.panels.values()))
        self.assertTrue(set(pg.panels["P1"].face_indices).isdisjoint(set(pg.panels["P2"].face_indices)))

    def test_fixture_4bend(self):
        """T2: Resolver populates disjoint face_indices for all 5 panels on Stepped Busbar."""
        shape = create_4bend_stepped_busbar(40.0, 30.0, 50.0, 30.0, 40.0)
        pg = PanelGraph()
        pids = ["P1", "P2", "P3", "P4", "P5"]
        for pid in pids:
            pg.add_panel(Panel(pid))
        pg.add_bend_connection("B1", "P1", "P2")
        pg.add_bend_connection("B2", "P2", "P3")
        pg.add_bend_connection("B3", "P3", "P4")
        pg.add_bend_connection("B4", "P4", "P5")

        canonical_payload = {
            "thickness_mm": {"value": 1.5},
            "panels": {
                "P1": {"centroid": [20.0, 25.0, 0.75], "normal": [0.0, 0.0, 1.0]},
                "P2": {"centroid": [41.75, 25.0, 17.5], "normal": [1.0, 0.0, 0.0]},
                "P3": {"centroid": [68.5, 25.0, 34.25], "normal": [0.0, 0.0, 1.0]},
                "P4": {"centroid": [95.25, 25.0, 51.0], "normal": [1.0, 0.0, 0.0]},
                "P5": {"centroid": [117.0, 25.0, 67.75], "normal": [0.0, 0.0, 1.0]},
            },
            "bends": {
                "B1": {"axis_point": [40.0, 0.0, 2.5], "child_panel_id": "P2"},
                "B2": {"axis_point": [43.5, 0.0, 32.5], "child_panel_id": "P3"},
                "B3": {"axis_point": [93.5, 0.0, 36.0], "child_panel_id": "P4"},
                "B4": {"axis_point": [97.0, 0.0, 66.0], "child_panel_id": "P5"},
            }
        }

        resolve_panel_faces(shape, pg, canonical_payload)
        for pid in pids:
            self.assertTrue(len(pg.panels[pid].face_indices) > 0, f"Panel {pid} must have face_indices")

        all_indices = []
        for pid in pids:
            all_indices.extend(pg.panels[pid].face_indices)
        self.assertEqual(len(set(all_indices)), sum(len(pg.panels[pid].face_indices) for pid in pids))

    def test_magicmock_shape_does_not_hang(self):
        """T3: MagicMock shape returns safely without hanging and leaves face_indices empty (<1s)."""
        mock_shape = MagicMock()
        pg = PanelGraph()
        pg.add_panel(Panel("P1"))
        pg.add_panel(Panel("P2"))
        canonical_payload = {
            "thickness_mm": {"value": 1.5},
            "panels": {"P1": {}, "P2": {}},
            "bends": {}
        }

        t0 = time.time()
        resolve_panel_faces(mock_shape, pg, canonical_payload)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 1.0, "Resolver must not hang on MagicMock shape")
        self.assertEqual(pg.panels["P1"].face_indices, [])
        self.assertEqual(pg.panels["P2"].face_indices, [])

    def test_preserve_existing_face_indices(self):
        """T4: Preserve existing non-empty face_indices without overwrite."""
        shape = create_l_bracket_shape(90.0, 1.0, 1.5, 50.0, 95.5, 47.5)
        pg = PanelGraph()
        p1 = Panel("P1", face_indices=[42])
        p2 = Panel("P2")
        pg.add_panel(p1)
        pg.add_panel(p2)

        canonical_payload = {
            "thickness_mm": {"value": 1.5},
            "panels": {
                "P1": {"centroid": [47.75, 25.0, 0.75], "normal": [0.0, 0.0, 1.0]},
                "P2": {"centroid": [97.25, 25.0, 26.25], "normal": [1.0, 0.0, 0.0]},
            },
            "bends": {}
        }
        resolve_panel_faces(shape, pg, canonical_payload)
        self.assertEqual(pg.panels["P1"].face_indices, [42])
        self.assertTrue(len(pg.panels["P2"].face_indices) > 0)


if __name__ == "__main__":
    unittest.main()
