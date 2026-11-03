"""Headless checks for PanelBuilder's fail-closed adjacency decisions."""
import unittest
from unittest.mock import patch

from freecad_smbs.integration.panel_builder import PanelBuildError, _side_panel


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


if __name__ == "__main__":
    unittest.main()
