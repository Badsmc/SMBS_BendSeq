import unittest
import os
import json
from unittest.mock import patch
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Core.orchestrator import BendSeqOrchestrator

REFERENCE_DIR = os.path.join(os.path.dirname(__file__), "..", "SMBendTests", "reference")
os.makedirs(REFERENCE_DIR, exist_ok=True)

MOCK_FEATURES = {
    "L": {
        "thickness": 2.0,
        "k_factor": 0.40,
        "material": "Steel_S235",
        "bends": {
            "BEND_01": {
                "origin": (0, 0, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_01"
            }
        }
    },
    "U": {
        "thickness": 2.0,
        "k_factor": 0.40,
        "material": "Steel_S235",
        "bends": {
            "BEND_01": {
                "origin": (0, -50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_01"
            },
            "BEND_02": {
                "origin": (0, 50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_01",
                "child_panel_id": "PANEL_02"
            }
        }
    },
    "Z": {
        "thickness": 2.0,
        "k_factor": 0.40,
        "material": "Steel_S235",
        "bends": {
            "BEND_01": {
                "origin": (0, -50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_01"
            },
            "BEND_02": {
                "origin": (0, 50, 0),
                "axis": (1, 0, 0),
                "angle": -90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_01",
                "child_panel_id": "PANEL_02"
            }
        }
    },
    "C": {
        "thickness": 2.0,
        "k_factor": 0.40,
        "material": "Steel_S235",
        "bends": {
            "BEND_01": {
                "origin": (0, -50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_01"
            },
            "BEND_02": {
                "origin": (0, 0, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_01",
                "child_panel_id": "PANEL_02"
            },
            "BEND_03": {
                "origin": (0, 50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_02",
                "child_panel_id": "PANEL_03"
            }
        }
    },
    "COMPLEX": {
        "thickness": 2.0,
        "k_factor": 0.40,
        "material": "Steel_S235",
        "bends": {
            "BEND_01": {
                "origin": (-50, 0, 0),
                "axis": (0, 1, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_01"
            },
            "BEND_02": {
                "origin": (0, -50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_02"
            },
            "BEND_03": {
                "origin": (50, 0, 0),
                "axis": (0, 1, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_03"
            },
            "BEND_04": {
                "origin": (0, 50, 0),
                "axis": (1, 0, 0),
                "angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_00",
                "child_panel_id": "PANEL_04"
            }
        }
    }
}

class TestRegressionSuites(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.orchestrator = BendSeqOrchestrator(backend=self.backend)

    def _run_and_verify(self, profile_name: str, expected_bends_count: int):
        target_shape = {"type": "mock_shape", "id": profile_name}
        
        with patch("smbs_core.Core.orchestrator.FeatureExtractor.extract_features") as mock_extract:
            mock_extract.return_value = MOCK_FEATURES[profile_name]
            
            output_file = os.path.join(REFERENCE_DIR, f"{profile_name}_ref.json")
            result = self.orchestrator.run_planning(
                target_shape=target_shape,
                algorithm="dfs",
                output_file=output_file,
                unknown_policy="EXPLORATORY"
            )
            
            self.assertTrue(result["success"] or result["status"] == "PARTIAL", f"Planning failed for {profile_name}: {result.get('error')}")
            self.assertEqual(len(result["sequence"]), expected_bends_count)
            self.assertTrue(os.path.exists(output_file))
            
            with open(output_file, 'r') as f:
                data = json.load(f)
                self.assertIn("sequence", data)
                self.assertEqual(len(data["sequence"]), expected_bends_count)

    def test_l_profile(self):
        self._run_and_verify("L", 1)

    def test_u_profile(self):
        self._run_and_verify("U", 2)

    def test_z_profile(self):
        self._run_and_verify("Z", 2)

    def test_c_profile(self):
        self._run_and_verify("C", 3)

    def test_complex_profile(self):
        self._run_and_verify("COMPLEX", 4)

if __name__ == "__main__":
    unittest.main()
