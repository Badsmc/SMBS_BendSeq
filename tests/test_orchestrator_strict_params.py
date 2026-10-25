import unittest
from unittest.mock import patch, MagicMock

from smbs_core.Core.orchestrator import BendSeqOrchestrator


def _create_minimal_bends_payload(**kwargs):
    payload = {
        "thickness": 2.0,
        "k_factor": 0.40,
        "bends": {
            "B1": {
                "bend_id": "B1",
                "origin": (0.0, 0.0, 0.0),
                "axis": (1.0, 0.0, 0.0),
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 50.0,
            }
        },
    }
    payload.update(kwargs)
    return payload


class TestOrchestratorStrictParams(unittest.TestCase):
    def setUp(self):
        backend = MagicMock()
        self.orch = BendSeqOrchestrator(backend)

    def test_kl18_raises_when_thickness_missing(self):
        """KL-18 inv#3: missing thickness in features must raise ValueError."""
        feats = _create_minimal_bends_payload()
        del feats["thickness"]
        with patch.object(self.orch.extractor, "extract_features", return_value=feats):
            with self.assertRaisesRegex(ValueError, "Orchestrator: features payload missing required 'thickness'"):
                self.orch.run_planning(target_shape=MagicMock())

    def test_kl18_raises_when_thickness_non_positive(self):
        """KL-18 inv#3: zero or negative thickness must raise ValueError."""
        feats = _create_minimal_bends_payload(thickness=0.0)
        with patch.object(self.orch.extractor, "extract_features", return_value=feats):
            with self.assertRaisesRegex(ValueError, "invalid non-positive thickness"):
                self.orch.run_planning(target_shape=MagicMock())

    def test_kl18_raises_when_k_factor_missing(self):
        """KL-18 inv#3: missing k_factor in features must raise ValueError."""
        feats = _create_minimal_bends_payload()
        del feats["k_factor"]
        with patch.object(self.orch.extractor, "extract_features", return_value=feats):
            with self.assertRaisesRegex(ValueError, "Orchestrator: features payload missing required 'k_factor'"):
                self.orch.run_planning(target_shape=MagicMock())

    def test_kl18_raises_when_k_factor_out_of_range(self):
        """KL-18 inv#3: k_factor outside [0.0, 1.0] must raise ValueError."""
        feats = _create_minimal_bends_payload(k_factor=1.5)
        with patch.object(self.orch.extractor, "extract_features", return_value=feats):
            with self.assertRaisesRegex(ValueError, "invalid k_factor=1.5"):
                self.orch.run_planning(target_shape=MagicMock())


if __name__ == "__main__":
    unittest.main()
