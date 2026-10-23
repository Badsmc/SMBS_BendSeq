import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from smbs_core.workers.topology.worker import TopologyWorker
from smbs_core.Core.bend_graph import IncompleteBendError
from smbs_core.protocols.file_store import ParameterSource, ConfidenceLevel


class TestTopologyWorkerRadiusLength(unittest.TestCase):
    """
    Unit tests for TopologyWorker strict radius/length requirements.
    Guards against silent 1.0/50.0 fallbacks with CONFIRMED provenance.
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

    def _valid_bend(self, **overrides):
        bend = {
            "bend_id": "BEND_01",
            "origin": [0.0, 0.0, 0.0],
            "axis": [1.0, 0.0, 0.0],
            "signed_angle": 90.0,
            "radius": 2.0,
            "length": 100.0,
            "parent_panel_id": "PANEL_A",
            "child_panel_id": "PANEL_B",
        }
        bend.update(overrides)
        return bend

    def test_01_missing_radius_raises(self):
        b = self._valid_bend()
        del b["radius"]
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features({"BEND_01": b})
        self.assertIn("radius", str(ctx.exception))

    def test_02_none_radius_raises(self):
        b = self._valid_bend(radius=None)
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features({"BEND_01": b})
        self.assertIn("radius", str(ctx.exception))

    def test_03_missing_length_raises(self):
        b = self._valid_bend()
        del b["length"]
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features({"BEND_01": b})
        self.assertIn("length", str(ctx.exception))

    def test_04_none_length_raises(self):
        b = self._valid_bend(length=None)
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_worker_with_features({"BEND_01": b})
        self.assertIn("length", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
