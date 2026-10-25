"""
Contract test for SheetMetalAdapter.
Complies with technical specification v1.2 §3.1, §3.3 (Rules R1-R5) and Principles 1, 16, 23.
"""

import sys
import unittest
from pathlib import Path

# Setup paths
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')

from smbs_core.workers.sheetmetal.adapter import SheetMetalAdapter, SheetMetalUnfoldResult
from smbs_core.protocols.file_store import RunStatus, ConfidenceLevel
from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar


class TestSheetMetalAdapterContract(unittest.TestCase):
    def setUp(self):
        self.adapter = SheetMetalAdapter()

    def test_availability(self):
        """Adapter correctly detects FreeCAD and SheetMetal environment."""
        self.assertTrue(self.adapter.is_available)
        self.assertIsNotNone(self.adapter.freecad_version)

    def test_unfold_4bend_stepped_busbar_contract(self):
        """Native unfold of 4-bend stepped busbar produces full valid contract."""
        import Part
        import tempfile
        import os
        from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

        # Create OCC shape and convert to Part.Shape
        occ_shape = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)
        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        try:
            writer = STEPControl_Writer()
            writer.Transfer(occ_shape, STEPControl_AsIs)
            writer.Write(step_path)
            fc_shape = Part.read(step_path)
        finally:
            if os.path.exists(step_path):
                os.remove(step_path)

        res: SheetMetalUnfoldResult = self.adapter.unfold(fc_shape, k_factor_override=0.44)

        # Assertions
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertIsNotNone(res.unfolded_shape)
        self.assertAlmostEqual(res.thickness_mm, 1.5, places=2)
        self.assertEqual(res.thickness_confidence, ConfidenceLevel.ESTIMATED)
        self.assertEqual(res.k_factor, 0.44)
        self.assertEqual(res.k_factor_confidence, ConfidenceLevel.CONFIRMED)

        # Check bend lines
        self.assertEqual(len(res.bend_lines), 4)
        for bend in res.bend_lines:
            self.assertIn(bend.direction, ["UP", "DOWN"])
            self.assertAlmostEqual(abs(bend.angle_deg), 90.0, places=1)
            self.assertIn(round(bend.radius_mm, 1), [1.0, 2.5])
            self.assertEqual(len(bend.start_point), 3)
            self.assertEqual(len(bend.end_point), 3)

    def test_backend_unavailable_never_returns_input_shape(self):
        """Rule R1: If backend is unavailable, adapter returns BACKEND_UNAVAILABLE, not input shape."""
        # Simulate unavailable adapter
        mock_adapter = SheetMetalAdapter()
        mock_adapter.is_available = False

        res = mock_adapter.unfold("dummy_shape")
        self.assertEqual(res.status, RunStatus.BACKEND_UNAVAILABLE)
        self.assertIsNone(res.unfolded_shape)
        self.assertTrue(any(d.code == "BACKEND_UNAVAILABLE" for d in res.diagnostics))


if __name__ == '__main__':
    unittest.main()
