import unittest
from smbs_core.Geometry.feature_extractor import FeatureExtractor, FeatureExtractionError
from smbs_core.Geometry.panel_topology import PanelTopologyBuilder
from smbs_core.geometry_backend.backends.mock_backend import MockBackend

class TestPhase4Extraction(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.extractor = FeatureExtractor(self.backend)

    def test_signed_angle_plus45_minus45_preserved(self):
        n1 = (0.0, 0.0, 1.0)
        n2 = (1.0, 0.0, 1.0)
        axis = (0.0, 1.0, 0.0)
        
        angle_plus = self.extractor.calculate_signed_angle(n1, n2, axis)
        angle_minus = self.extractor.calculate_signed_angle(n1, n2, (0.0, -1.0, 0.0))
        
        self.assertGreater(angle_plus, 0.0)
        self.assertLess(angle_minus, 0.0)
        self.assertEqual(abs(angle_plus), abs(angle_minus))

    def test_zero_angle_ambiguous_raises(self):
        n1 = (0.0, 0.0, 1.0)
        n2 = (0.0, 0.0, 1.0)
        axis = (1.0, 0.0, 0.0)
        
        with self.assertRaises(FeatureExtractionError):
            self.extractor.calculate_signed_angle(n1, n2, axis)

    def test_feature_extractor_none_shape_raises(self):
        with self.assertRaises(FeatureExtractionError):
            self.extractor.extract_features(None)

    def test_panel_topology_builder(self):
        bends_dict = {
            "BEND_01": {"parent_panel_id": "PANEL_A", "child_panel_id": "PANEL_B"},
            "BEND_02": {"parent_panel_id": "PANEL_B", "child_panel_id": "PANEL_C"}
        }
        builder = PanelTopologyBuilder(self.backend)
        graph = builder.build_panel_graph(None, bends_dict)
        self.assertIn("PANEL_A", graph.panels)
        self.assertIn("PANEL_B", graph.panels)
        self.assertIn("PANEL_C", graph.panels)
        self.assertEqual(graph.moving_subtree("BEND_01"), {"PANEL_B", "PANEL_C"})

if __name__ == "__main__":
    unittest.main()
