"""
Test for vertical slice: Coordinator -> Geometry Worker -> JSON / BREP artifacts.
Compliant with technical specification v1.2 §4.1, §4.4, §4.5 and Principles 1, 2, 8, 12, 13, 21, 24.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Paths
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.coordinator.run_pipeline import Coordinator
from smbs_core.protocols.file_store import compute_file_sha256, RunStatus
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar


class TestVerticalSliceGeometry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.coordinator = Coordinator(workspace_root=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vertical_slice_stepped_busbar(self):
        """Test vertical slice: Coordinator -> InputManifest -> GeometryWorker -> 02_geometry_result.json + geometry.brep."""
        import Part
        from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

        # 1. Create source STEP file in fixtures dir
        source_step = Path(self.temp_dir) / "busbar_4bend.step"
        occ_shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        writer = STEPControl_Writer()
        writer.Transfer(occ_shape, STEPControl_AsIs)
        writer.Write(str(source_step))

        # 2. Execute Coordinator vertical slice
        res = self.coordinator.start_run(
            source_file_path=source_step,
            part_id="busbar_4bend",
            input_mode="NATIVE_SHEETMETAL",
            k_factor_override=0.44
        )

        # 3. Assertions
        self.assertEqual(res["baseline_status"], "VALID", "Vertical slice: baseline must be VALID")
        self.assertTrue(res["pass"], "Vertical slice: baseline pass flag must be True")
        run_dir = Path(res["run_dir"])

        manifest_file = run_dir / "01_input_manifest.json"
        geom_result_file = run_dir / "02_geometry_result.json"
        brep_file = run_dir / "geometry.brep"
        flat_brep_file = run_dir / "flat_pattern.brep"

        self.assertTrue(manifest_file.exists())
        self.assertTrue(geom_result_file.exists())
        self.assertTrue(brep_file.exists())
        self.assertTrue(flat_brep_file.exists())

        # No .tmp files
        tmp_files = list(run_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

        # Binary content hash matches
        actual_brep_hash = compute_file_sha256(brep_file)
        payload = res["payload"]
        self.assertEqual(payload["geometry_artifact"]["content_hash"], actual_brep_hash)
        self.assertEqual(payload["thickness_mm"]["value"], 1.5)
        self.assertEqual(payload["detected_bends_count"], 4)
        self.assertGreater(payload["detected_faces_count"], 5)


if __name__ == '__main__':
    unittest.main()
