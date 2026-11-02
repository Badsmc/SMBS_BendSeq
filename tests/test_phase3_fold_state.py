"""
test_phase3_fold_state.py - Phase 3 Canonical Physical State Test Suite

Verifies LAW-3 FoldState & StateCache requirements:
- Construction of initial root FoldState.
- Unfold transition apply_unfold().
- Multi-property physical fingerprint uniqueness.
- Integration with StateCache.
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

from smbs_core.Core.bend_graph import BendRecord, BendGraph
from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.state_cache import StateCache


class TestPhase3FoldState(unittest.TestCase):
    """Phase 3 Canonical FoldState Tests."""

    def setUp(self):
        self.bend_graph = BendGraph()
        self.b1 = BendRecord("BEND_01", "F1", "PA", "PB", (0,0,0), (1,0,0), 90.0, 2.0, 2.0)
        self.b2 = BendRecord("BEND_02", "F2", "PB", "PC", (0,10,0), (0,1,0), 90.0, 2.0, 2.0)
        self.bend_graph.add_bend(self.b1)
        self.bend_graph.add_bend(self.b2)

        self.panel_graph = PanelGraph()
        self.panel_graph.add_bend_connection("BEND_01", "PA", "PB")
        self.panel_graph.add_bend_connection("BEND_02", "PB", "PC")

    def test_initial_fold_state(self):
        """Test initial root FoldState construction."""
        state = FoldState.initial(None, self.bend_graph, self.panel_graph)
        self.assertFalse(state.is_goal())
        self.assertEqual(state.remaining_bends, {"BEND_01", "BEND_02"})
        self.assertEqual(state.folded_bends, {"BEND_01", "BEND_02"})
        self.assertEqual(state.bend_transforms["BEND_01"], 90.0)

    def test_apply_unfold_transition(self):
        """Test unfolding a bend updates FoldState properties."""
        root = FoldState.initial(None, self.bend_graph, self.panel_graph)
        moving = self.panel_graph.moving_subtree("BEND_01")
        
        s1 = root.apply_unfold("BEND_01", None, moving)
        self.assertFalse(s1.is_goal())
        self.assertEqual(s1.remaining_bends, {"BEND_02"})
        self.assertEqual(s1.bend_transforms["BEND_01"], 0.0)

        s2 = s1.apply_unfold("BEND_02", None, {"PC"})
        self.assertTrue(s2.is_goal())
        self.assertEqual(len(s2.remaining_bends), 0)

    def test_fingerprint_uniqueness(self):
        """Test that different physical states produce distinct fingerprints."""
        s1 = FoldState.initial(None, self.bend_graph, self.panel_graph)
        s2 = s1.apply_unfold("BEND_01", None, {"PB", "PC"})

        self.assertNotEqual(s1.fingerprint(), s2.fingerprint())

        # Change tooling state -> fingerprint must change
        s3 = FoldState(
            shape=None,
            folded_bends={"BEND_01"},
            remaining_bends={"BEND_01"},
            tooling_state={"punch_id": "PUNCH_SPECIAL", "die_id": "DEFAULT_DIE"}
        )
        self.assertNotEqual(s1.fingerprint(), s3.fingerprint())

    def test_state_cache_integration(self):
        """Test StateCache using FoldState fingerprints."""
        cache = StateCache()
        root = FoldState.initial(None, self.bend_graph, self.panel_graph)
        key = cache.compute_hash(root)

        self.assertFalse(cache.contains(key))
        cache.put(key, {"visited": True})
        self.assertTrue(cache.contains(key))
        self.assertEqual(cache.get(key), {"visited": True})


if __name__ == "__main__":
    unittest.main()
