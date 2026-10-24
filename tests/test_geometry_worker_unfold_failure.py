import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt

from smbs_core.workers.geometry.worker import GeometryWorker
from smbs_core.workers.sheetmetal.adapter import SheetMetalUnfoldResult
from smbs_core.contracts.models import DiagnosticEntry
from smbs_core.protocols.file_store import ParameterSource, ConfidenceLevel, RunStatus


class TestGeometryWorkerUnfoldFailure(unittest.TestCase):
    """
    Unit tests for GeometryWorker gating status on unfold failures
    and FreeCAD SheetMetal backend unavailability (P0-CODE-4).
    """

    def _make_worker(self, input_mode: str, is_available: bool = True, unfold_result=None):
        worker = GeometryWorker()
        worker.file_store = MagicMock()
        worker.file_store.read_json_verified.return_value = {
            "run_id": "test_run",
            "part_id": "test_part",
            "input_fingerprint": "abc",
            "payload": {
                "input_mode": input_mode,
                "source_file_path": "dummy.step",
                "thickness_mm": {
                    "value": 2.0,
                    "source": ParameterSource.SHEETMETAL_NATIVE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
            }
        }
        worker.file_store.write_json_atomic = MagicMock()
        # Mock _load_occ_shape to return a real valid box
        worker._load_occ_shape = MagicMock(return_value=BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), gp_Pnt(20, 20, 20)).Shape())
        worker.sheetmetal_adapter = MagicMock()
        worker.sheetmetal_adapter.is_available = is_available
        if unfold_result is not None:
            worker.sheetmetal_adapter.unfold.return_value = unfold_result
        return worker

    def _run(self, worker):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest_file = Path(tmp_dir) / "manifest.json"
            with patch.object(Path, "exists", return_value=True), patch.object(Path, "unlink", return_value=None):
                worker.execute(str(manifest_file))
            saved = worker.file_store.write_json_atomic.call_args
            return saved[0][1]

    def test_01_backend_unavailable_marks_backend_unavailable(self):
        worker = self._make_worker("NATIVE_SHEETMETAL", is_available=False)
        payload = self._run(worker)
        self.assertEqual(payload["status"], "BACKEND_UNAVAILABLE")
        self.assertEqual(payload["payload"]["geometry_status"], "BACKEND_UNAVAILABLE")

    def test_02_unfold_error_marks_worker_error(self):
        fake_part = MagicMock()
        fake_part.read.return_value = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), gp_Pnt(20, 20, 20)).Shape()

        err_result = SheetMetalUnfoldResult(
            status=RunStatus.WORKER_ERROR,
            diagnostics=[DiagnosticEntry(code="UNFOLD_FAILED", message="Failed to unfold", level="ERROR", stage="unfold")]
        )
        worker = self._make_worker("NATIVE_SHEETMETAL", is_available=True, unfold_result=err_result)

        with patch.dict(sys.modules, {"Part": fake_part}):
            with patch("smbs_core.workers.geometry.worker.STEPControl_Writer") as mock_writer_cls:
                mock_writer = mock_writer_cls.return_value
                mock_writer.Transfer = MagicMock()
                mock_writer.Write = MagicMock()
                payload = self._run(worker)

        self.assertEqual(payload["status"], "WORKER_ERROR")
        self.assertEqual(payload["payload"]["geometry_status"], "WORKER_ERROR")
        diag_codes = [d["code"] for d in payload["diagnostics"]]
        self.assertIn("UNFOLD_FAILED", diag_codes)

    def test_03_canonical_mode_marks_success(self):
        worker = self._make_worker("CANONICAL")
        payload = self._run(worker)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["payload"]["geometry_status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
