import unittest
import sys
import subprocess
from smbs_core.IO.bendseq_writer import BendSeqWriter

class TestPhase10CLI(unittest.TestCase):
    def test_json_schema_writer(self):
        writer = BendSeqWriter()
        metadata = {"status": "SUCCESS", "model_id": "test_part"}
        seq = [{
            "step": 1,
            "bend_id": "B1",
            "status": "VALID",
            "collision_status": "VALID",
            "tooling_status": "REAL",
            "alignment_status": "ALIGNED",
            "angle_deg": 90.0,
            "radius_mm": 2.0,
            "length_mm": 100.0,
            "axis": [1, 0, 0],
            "origin": [0, 0, 0]
        }]
        path = writer.write_bendseq("test_output.json", metadata, seq)
        self.assertTrue(path.endswith("test_output.json"))

    def test_cli_returns_failed_when_missing_file(self):
        cmd = [sys.executable, "-m", "smbs_core.cli", "non_existent_part.step"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("not found", res.stderr.lower() + res.stdout.lower())

if __name__ == "__main__":
    unittest.main()
