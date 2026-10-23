import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from smbs_core.workers.topology.worker import TopologyWorker
from smbs_core.Core.bend_graph import IncompleteBendError
from smbs_core.protocols.file_store import ParameterSource, ConfidenceLevel


class TestTopologyWorkerDirection(unittest.TestCase):
    """
    Unit tests for TopologyWorker direction determination and strict axis/origin requirements.
    Guards against silent fallbacks and verifies invariant preservation under signed_angle flips.
    """

    def _run_worker_with_features(self, raw_bends: dict, raw_panels: dict = None) -> dict:
        if raw_panels is None:
            raw_panels = {
                "PANEL_A": {"total_area": 1000.0, "centroid": [0, 0, 0], "normal": [0, 0, 1]},
                "PANEL_B": {"total_area": 1000.0, "centroid": [50, 0, 0], "normal": [0, 1, 0]},
            }

        worker = TopologyWorker()
        worker.file_store = MagicMock()
        worker.file_store.read_json_verified.return_value = {
            "run_id": "test_run",
            "part_id": "test_part",
            "input_fingerprint": "abc",
            "payload": {
                "geometry_artifact": {"file_path": "dummy.brep"},
                "thickness_mm": {
                    "value": 2.0,
                    "source": ParameterSource.SHEETMETAL_NATIVE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
                "k_factor": {"value": 0.44}
            }
        }
        worker._load_brep = MagicMock(return_value={"type": "mock_box"})
        worker.extractor = MagicMock()
        worker.extractor.extract_features.return_value = {
            "thickness": 2.0,
            "bends": raw_bends,
            "panels": raw_panels,
            "records": {}
        }
        worker.file_store.write_json_atomic = MagicMock()

        with patch.object(Path, "exists", return_value=True):
            worker.execute("dummy_geometry_result.json")

        saved_call = worker.file_store.write_json_atomic.call_args
        return saved_call[0][1]["payload"]

    def test_01_up_sign_maps_to_up(self):
        raw_bends = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "origin": [0.0, 0.0, 0.0],
                "axis": [1.0, 0.0, 0.0],
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        payload = self._run_worker_with_features(raw_bends)
        bend = payload["bends"]["BEND_01"]
        self.assertEqual(bend["direction"]["value"], "UP")
        self.assertEqual(bend["direction"]["source"], ParameterSource.GEOMETRIC_INFERENCE.value)
        self.assertEqual(bend["direction"]["confidence"], ConfidenceLevel.CONFIRMED.value)
        self.assertEqual(bend["axis_point"], [0.0, 0.0, 0.0])
        self.assertEqual(bend["axis_direction"], [1.0, 0.0, 0.0])

    def test_02_down_sign_maps_to_down(self):
        raw_bends = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "origin": [10.0, 20.0, 30.0],
                "axis": [0.0, 1.0, 0.0],
                "signed_angle": -45.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        payload = self._run_worker_with_features(raw_bends)
        bend = payload["bends"]["BEND_01"]
        self.assertEqual(bend["direction"]["value"], "DOWN")
        self.assertEqual(bend["direction"]["source"], ParameterSource.GEOMETRIC_INFERENCE.value)
        self.assertEqual(bend["direction"]["confidence"], ConfidenceLevel.CONFIRMED.value)

    def test_03_missing_axis_raises(self):
        raw_bends_missing = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "origin": [0.0, 0.0, 0.0],
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features(raw_bends_missing)
        self.assertIn("axis", str(ctx.exception))

        raw_bends_none = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "origin": [0.0, 0.0, 0.0],
                "axis": None,
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features(raw_bends_none)
        self.assertIn("axis", str(ctx.exception))

    def test_04_missing_origin_raises(self):
        raw_bends_missing = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "axis": [1.0, 0.0, 0.0],
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features(raw_bends_missing)
        self.assertIn("origin", str(ctx.exception))

        raw_bends_none = {
            "BEND_01": {
                "bend_id": "BEND_01",
                "origin": None,
                "axis": [1.0, 0.0, 0.0],
                "signed_angle": 90.0,
                "radius": 2.0,
                "length": 100.0,
                "parent_panel_id": "PANEL_A",
                "child_panel_id": "PANEL_B",
            }
        }
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features(raw_bends_none)
        self.assertIn("origin", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
