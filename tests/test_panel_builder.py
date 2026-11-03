"""Headless checks for PanelBuilder's fail-closed adjacency decisions."""
import unittest
from unittest.mock import patch

from freecad_smbs.integration.panel_builder import (
    Panel,
    PanelBuildError,
    PanelBuildResult,
    PanelConnection,
    _side_panel,
)
from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import graph_from_panel_build
from smbs_core.kinematics2d.panel_kinematics import FoldState


class PanelBuilderAdjacencyTests(unittest.TestCase):
    def test_two_sides_resolve_to_different_panels(self):
        faces = (object(), object())
        def contains(face, point, tol):
            return (face is faces[0]) if point[1] > 0 else (face is faces[1])
        with patch("freecad_smbs.integration.panel_builder._contains", side_effect=contains):
            self.assertEqual(_side_panel(faces, (0, 0), (10, 0), 1, 0.1, 1e-6), 0)
            self.assertEqual(_side_panel(faces, (0, 0), (10, 0), -1, 0.1, 1e-6), 1)

    def test_ambiguous_probe_is_rejected(self):
        with patch("freecad_smbs.integration.panel_builder._contains", return_value=True):
            with self.assertRaises(PanelBuildError):
                _side_panel((object(), object()), (0, 0), (10, 0), 1, 0.1, 1e-6)

    def test_uncovered_probe_is_rejected(self):
        with patch("freecad_smbs.integration.panel_builder._contains", return_value=False):
            with self.assertRaises(PanelBuildError):
                _side_panel((object(),), (0, 0), (10, 0), 1, 0.1, 1e-6)

    def test_graph_from_panel_build_propagates_bend_zone_and_moving_side(self):
        params = bend_parameters(-90.0, 2.5, 1.5, 0.4)
        zone = make_bend_zone("B01", ((0.0, 48.75663706143593), (200.0, 48.75663706143593)), params)
        panels = (
            Panel("P01", 9500.0, (100.0, 23.75)),
            Panel("P02", 9500.0, (100.0, 73.76327412287185)),
        )
        # panel_a is the +left (+Y) side = P02; panel_b is the -Y side = P01
        conns = (
            PanelConnection(
                "B01",
                "P02",
                "P01",
                params.fold_angle_deg,
                2.5,
                zone.axis_xy,
                zone,
            ),
        )
        res = PanelBuildResult(panels, conns, (), (), (zone,), ())
        g = graph_from_panel_build(res)
        rooted = g.rooted("P02")
        self.assertEqual(len(rooted), 1)
        self.assertIs(rooted[0].bend_zone, zone)
        self.assertFalse(rooted[0].moving_left)
        state = FoldState(g, "P02")
        state.set_angle("B01", params.fold_angle_deg)
        c_folded = state.transforms["P01"].point((100.0, 23.75, 0.0))
        self.assertAlmostEqual(c_folded[0], 100.0, places=9)
        self.assertAlmostEqual(c_folded[1], 47.51327412287185, places=9)
        self.assertAlmostEqual(c_folded[2], -26.25, places=9)


if __name__ == "__main__":
    unittest.main()
