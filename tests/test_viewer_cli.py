"""
test_viewer_cli.py - Tests for S.14 viewer CLI.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smbs_core.viewer.cli import main


class TestViewerCLI(unittest.TestCase):
    def setUp(self):
        self.real_verified_run = Path("runs/run_20261012_034042_cf4c34")
        self.real_failed_run = Path("runs/run_20261017_203828_511545")

    def _write_brep(self, path: Path) -> bool:
        try:
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCC.Core.BRepTools import breptools
        except ImportError:
            return False
        box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
        breptools.Write(box, str(path))
        return True

    def test_cli_view_real_run_exit_0(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_verified_run)])
            self.assertEqual(code, 0)
            out = mock_out.getvalue()
            self.assertIn("verification_status: VERIFIED", out)
            self.assertIn("production_eligible: true", out)
            self.assertIn("backend: OCC", out)

    def test_cli_view_missing_run_dir_exit_2(self):
        missing_dir = Path("/nonexistent/run_dir_123")
        with patch("sys.stderr", new_callable=io.StringIO):
            code = main(["view", str(missing_dir)])
            self.assertEqual(code, 2)

    def test_cli_view_missing_04_exit_2(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="test_cli_no_04_"))
        with patch("sys.stderr", new_callable=io.StringIO):
            code = main(["view", str(tmp_dir)])
            self.assertEqual(code, 2)

    def test_cli_json_output_parsable(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_verified_run), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(mock_out.getvalue())
            self.assertEqual(data["verification_status"], "VERIFIED")
            self.assertTrue(data["production_eligible"])
            self.assertIn("backend_kind", data)
            self.assertEqual(data["backend_kind"], "OCC")
            self.assertIn("frames", data)
            self.assertGreater(len(data["frames"]), 0)

    def test_cli_dump_frames_creates_files(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        tmp_dump = Path(tempfile.mkdtemp(prefix="dump_frames_"))
        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["view", str(self.real_verified_run), "--dump-frames", str(tmp_dump)])
            self.assertEqual(code, 0)

        dumped_files = sorted(list(tmp_dump.glob("frame_*.json")))
        self.assertGreater(len(dumped_files), 1)

    def test_cli_dump_frames_no_shape(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        tmp_dump = Path(tempfile.mkdtemp(prefix="dump_frames_"))
        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["view", str(self.real_verified_run), "--dump-frames", str(tmp_dump)])
            self.assertEqual(code, 0)

        for f_path in tmp_dump.glob("frame_*.json"):
            data = json.loads(f_path.read_text())
            self.assertNotIn("shape", data)
            self.assertIn("frame_index", data)
            self.assertIn("geometry_precision", data)
            self.assertIn("state_summary", data)
            self.assertIn("backend_kind", data)

    def test_cli_table_includes_verification_status(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_verified_run)])
            self.assertEqual(code, 0)
            out = mock_out.getvalue()
            self.assertIn("verification_status:", out)
            self.assertIn("production_eligible:", out)

    def test_cli_unverified_run(self):
        tmp_run = Path(tempfile.mkdtemp(prefix="unverified_run_"))
        geom = tmp_run / "geometry.brep"
        
        if not self._write_brep(geom):
            self.skipTest("OCC not available for creating test BREP fixture")

        c4 = {
            "schema_name": "canonical_part",
            "schema_version": "1.0",
            "run_id": "r_unverif",
            "part_id": "p_unverif",
            "units": "mm",
            "payload": {
                "thickness_mm": {"value": 1.0},
                "geometry_artifact": {"file_path": str(geom)},
                "panels": {"P1": {"normal": [0, 0, 1]}},
                "bends": {},
            },
        }
        s7 = {
            "schema_name": "search_result",
            "schema_version": "1.0",
            "run_id": "r_unverif",
            "part_id": "p_unverif",
            "units": "mm",
            "payload": {"planning_status": "SUCCESS", "candidate_sequence": []},
        }
        (tmp_run / "04_canonical_part.json").write_text(json.dumps(c4))
        (tmp_run / "07_search_result.json").write_text(json.dumps(s7))

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(tmp_run)])
            self.assertEqual(code, 0)
            out = mock_out.getvalue()
            self.assertIn("verification_status: UNVERIFIED", out)
            self.assertIn("production_eligible: null", out)

    def test_cli_verification_failed_run(self):
        if not self.real_failed_run.is_dir():
            self.skipTest("real failed run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_failed_run)])
            self.assertEqual(code, 0)
            out = mock_out.getvalue()
            self.assertIn("verification_status: VERIFICATION_FAILED", out)
            self.assertIn("production_eligible: false", out)

    def test_cli_verification_verified_run(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_verified_run)])
            self.assertEqual(code, 0)
            out = mock_out.getvalue()
            self.assertIn("verification_status: VERIFIED", out)
            self.assertIn("production_eligible: true", out)

    def test_cli_direction_forward_exit_1(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            code = main(["view", str(self.real_verified_run), "--direction", "forward"])
            self.assertEqual(code, 1)
            err = mock_err.getvalue()
            self.assertIn("KL-12", err)

    def test_cli_occ_unavailable_without_flag_exit_2(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch.dict("sys.modules", {"smbs_core.geometry_backend.backends.pythonocc_backend": None}):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                code = main(["view", str(self.real_verified_run)])
                self.assertEqual(code, 2)
                err = mock_err.getvalue()
                self.assertIn("Fatal: OCC backend unavailable", err)
                self.assertIn("--allow-mock-backend", err)

    def test_cli_occ_unavailable_with_mock_flag_exit_0(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch.dict("sys.modules", {"smbs_core.geometry_backend.backends.pythonocc_backend": None}):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                    code = main(["view", str(self.real_verified_run), "--allow-mock-backend"])
                    self.assertEqual(code, 0)
                    err = mock_err.getvalue()
                    self.assertIn("WARNING: --allow-mock-backend requested", err)
                    out = mock_out.getvalue()
                    self.assertIn("backend: MOCK (preview)", out)

    def test_cli_json_includes_backend_kind(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["view", str(self.real_verified_run), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(mock_out.getvalue())
            self.assertIn("backend_kind", data)
            self.assertIn(data["backend_kind"], {"OCC", "MOCK"})

    def test_cli_dump_frames_includes_backend_kind(self):
        if not self.real_verified_run.is_dir():
            self.skipTest("real verified run not found")

        tmp_dump = Path(tempfile.mkdtemp(prefix="dump_frames_backend_"))
        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["view", str(self.real_verified_run), "--dump-frames", str(tmp_dump)])
            self.assertEqual(code, 0)

        for f_path in tmp_dump.glob("frame_*.json"):
            data = json.loads(f_path.read_text())
            self.assertIn("backend_kind", data)
            self.assertEqual(data["backend_kind"], "OCC")


if __name__ == "__main__":
    unittest.main()
