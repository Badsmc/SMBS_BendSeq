from unittest.mock import patch
import unittest
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Core.bend_graph import IncompleteBendError


class TestFeatureExtractorSignedAngle(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.extractor = FeatureExtractor(self.backend)

    def test_feature_extractor_returns_signed_angle(self):
        """Verify that feature_extractor returns signed_angle, angle_deg, angle as floats."""
        shape = self.backend.make_box(100, 50, 2)
        feats = self.extractor.extract_features(shape)
        self.assertIn("bends", feats)
        self.assertGreater(len(feats["bends"]), 0)

        for b_id, b_info in feats["bends"].items():
            self.assertIn("signed_angle", b_info)
            self.assertIsInstance(b_info["signed_angle"], float)
            self.assertIn("angle_deg", b_info)
            self.assertIsInstance(b_info["angle_deg"], float)
            self.assertIn("angle", b_info)
            self.assertIsInstance(b_info["angle"], float)
            self.assertEqual(b_info["angle"], abs(b_info["signed_angle"]))

    def test_incomplete_bend_error_raised_when_angle_missing(self):
        """Verify that orchestrator raises IncompleteBendError if bend has no angle."""
        from smbs_core.Core.orchestrator import BendSeqOrchestrator
        orch = BendSeqOrchestrator(self.backend)
        # Mock extractor that returns bend without any angle field
        bad_feats = {
            "thickness": 2.0,
            "material": "Steel",
            "bends": {
                "B_BAD": {
                    "bend_id": "B_BAD",
                    "origin": (0, 0, 0),
                    "axis": (1, 0, 0),
                    # no signed_angle, angle, or signed_angle_deg
                    "radius": 2.0,
                    "length": 50.0
                }
            },
            "records": {}
        }
        with patch.object(orch.extractor, "extract_features", return_value=bad_feats):
            with self.assertRaises(IncompleteBendError):
                orch.run_planning(target_shape={"type": "mock_shape"})


if __name__ == "__main__":
    unittest.main()
