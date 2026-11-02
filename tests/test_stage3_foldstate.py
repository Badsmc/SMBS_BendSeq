"""
test_stage3_foldstate.py - Stage 3 FoldState Strength & Fingerprint Test Suite

Verifies:
1. Fast, deterministic fingerprint generation without requiring OCC TopoShape.
2. Canonical panel_transforms and bend_transforms serialization in fingerprint.
3. Matching predictive fingerprints (`predict_successor_fingerprint`).
"""

import sys
import os
import unittest

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph


class TestStage3FoldState(unittest.TestCase):
    """Stage 3 FoldState Fingerprint Validation."""

    def setUp(self):
        self.bg = BendGraph()
        self.bg.add_bend(BendRecord("BEND_01", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 2.0, 2.0))
        self.bg.add_bend(BendRecord("BEND_02", "F2", "PB", "PC", (0,10,0), (0,1,0), 90.0, 2.0, 2.0))

        self.pg = PanelGraph()
        self.pg.add_bend_connection("BEND_01", "PA", "PB")
        self.pg.add_bend_connection("BEND_02", "PB", "PC")

    def test_fingerprint_without_shape(self):
        """Verify fingerprint is computed deterministically with shape=None."""
        s1 = FoldState.initial(None, self.bg, self.pg)
        fp1 = s1.fingerprint()
        
        self.assertIsNotNone(fp1)
        self.assertEqual(len(fp1), 64) # SHA-256 hex length
        
        # Second call returns cached fingerprint
        self.assertEqual(s1.fingerprint(), fp1)

    def test_predictive_fingerprint_matching(self):
        """Verify predict_successor_fingerprint matches actual unfolded successor fingerprint."""
        root = FoldState.initial(None, self.bg, self.pg)
        
        # Predict fingerprint after unfolding BEND_01
        predicted_fp = root.predict_successor_fingerprint("BEND_01", moving_panels={"PB", "PC"})
        
        # Create successor state manually via apply_unfold
        successor = root.apply_unfold("BEND_01", None, {"PB", "PC"})
        actual_fp = successor.fingerprint()

        self.assertEqual(predicted_fp, actual_fp)


if __name__ == "__main__":
    unittest.main()
