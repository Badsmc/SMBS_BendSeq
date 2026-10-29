"""
test_manual_sequence_panel.py - Phase 3 Interactive Panel Tests with Emulated Selection.

Verifies:
1. P1: SelectionObserver handles Edge selection on Unfold and filters out non-bend elements.
2. B2-A: BendSelection stores immutable RawBendLineSnapshot with bend_id=None at click time.
3. P1 toggle: clicking an already selected bend removes it from the sequence.
4. P2: mark_stale marks selections stale and clears any temporary IDs.
5. P3: non-bend edge click triggers status warning and is ignored.
6. P4: cancel flag is respected when _on_cancel is triggered.
7. Reordering: moving items up and down maintains 1-based order continuity.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import Part

from freecad_smbs.ui.manual_sequence_panel import (
    ManualSequencePanel,
    BendSelectionItem,
    RawBendLineSnapshot,
)
from tests.fixtures.create_test_part import FIXTURES_DIR


class TestManualSequencePanel(unittest.TestCase):
    """Test suite for Phase 3 ManualSequencePanel."""

    @classmethod
    def setUpClass(cls):
        cls.box_path = FIXTURES_DIR / "test_box.FCStd"
        try:
            from PySide import QtGui
            cls.app = QtGui.QApplication.instance() or QtGui.QApplication(["SMBS_Test"])
        except ImportError:
            try:
                from PySide2 import QtWidgets
                cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["SMBS_Test"])
            except ImportError:
                cls.app = None

    def setUp(self):
        self.doc = App.openDocument(str(self.box_path))
        self.panel = ManualSequencePanel(initial_doc=self.doc)

    def tearDown(self):
        if self.panel:
            self.panel.reject()
        App.closeDocument(self.doc.Name)

    def test_emulated_edge_selection_and_snapshot(self):
        """P1 & B2-A: Adding valid bend edges creates BendSelectionItem with RawBendLineSnapshot."""
        unfold = self.doc.getObject("Unfold")
        self.assertIsNotNone(unfold)

        # Find the 3 internal bend edges on unfold (length 100, 80, 100)
        bend_sub_names = []
        for i in range(len(unfold.Shape.Edges)):
            sub = f"Edge{i+1}"
            edge = unfold.Shape.getElement(sub)
            if edge and round(edge.Length, 1) in (80.0, 100.0) and abs(edge.CenterOfMass.z - 1.5) < 0.1:
                # Internal bend lines: Y=0, Y=80, X=0
                m = edge.CenterOfMass
                if (abs(m.y - 0.0) < 0.1 and abs(edge.Length - 100.0) < 0.1) or \
                   (abs(m.y - 80.0) < 0.1 and abs(edge.Length - 100.0) < 0.1) or \
                   (abs(m.x - 0.0) < 0.1 and abs(edge.Length - 80.0) < 0.1):
                    bend_sub_names.append(sub)

        self.assertGreaterEqual(len(bend_sub_names), 3)

        # Emulate click on 3 edges in sequence:
        target_subs = bend_sub_names[:3]
        for sub in target_subs:
            self.panel.addSelection(self.doc.Name, unfold.Name, sub, None)

        selections = self.panel.get_selections()
        self.assertEqual(len(selections), 3, f"Must select exactly 3 bend edges, got {len(selections)}")

        # Verify B2-A snapshot properties
        for idx, sel in enumerate(selections):
            self.assertEqual(sel.order, idx + 1)
            self.assertIsNone(sel.bend_id, "bend_id must be None at click time prior to Phase 4")
            self.assertIsInstance(sel.raw_snapshot, RawBendLineSnapshot)
            self.assertGreater(sel.raw_snapshot.length_mm, 50.0)
            self.assertEqual(len(sel.raw_snapshot.midpoint), 3)

    def test_toggle_already_selected_edge(self):
        """Clicking an already selected edge removes it from sequence."""
        unfold = self.doc.getObject("Unfold")
        
        # Select Edge 23 (or known bend line)
        for i in range(len(unfold.Shape.Edges)):
            sub = f"Edge{i+1}"
            edge = unfold.Shape.getElement(sub)
            if edge and round(edge.Length, 1) == 80.0 and abs(edge.CenterOfMass.x) < 0.1:
                bend_sub = sub
                break

        # Click 1: Add
        self.panel.addSelection(self.doc.Name, unfold.Name, bend_sub, None)
        self.assertEqual(len(self.panel.get_selections()), 1)

        # Click 2: Toggle remove
        self.panel.addSelection(self.doc.Name, unfold.Name, bend_sub, None)
        self.assertEqual(len(self.panel.get_selections()), 0, "Second click must toggle remove")

    def test_non_bend_edge_ignored(self):
        """P3: Non-bend perimeter edge click is ignored with a warning status."""
        unfold = self.doc.getObject("Unfold")
        # Find 1.5mm thickness edge (not a bend line)
        short_sub = None
        for i in range(len(unfold.Shape.Edges)):
            sub = f"Edge{i+1}"
            edge = unfold.Shape.getElement(sub)
            if edge and round(edge.Length, 1) == 1.5:
                short_sub = sub
                break

        self.assertIsNotNone(short_sub)
        self.panel.addSelection(self.doc.Name, unfold.Name, short_sub, None)
        self.assertEqual(len(self.panel.get_selections()), 0, "Non-bend edge must not be added")
        self.assertIn("Not a bend line", self.panel.status.text())

    def test_face_and_vertex_ignored(self):
        """P1: SubElements that are not edges are silently ignored."""
        unfold = self.doc.getObject("Unfold")
        self.panel.addSelection(self.doc.Name, unfold.Name, "Face1", None)
        self.panel.addSelection(self.doc.Name, unfold.Name, "Vertex1", None)
        self.assertEqual(len(self.panel.get_selections()), 0)

    def test_reorder_and_cancel(self):
        """Tests item reordering (up/down) and cancellation flag (P4)."""
        # Emulate 2 items
        snap1 = RawBendLineSnapshot((0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 100.0)
        snap2 = RawBendLineSnapshot((0, 80, 0), (1, 0, 0), (0, 0, 1), 1.0, 100.0)
        self.panel._selections = [
            BendSelectionItem(1, "EdgeA", "Unfold", snap1),
            BendSelectionItem(2, "EdgeB", "Unfold", snap2),
        ]
        self.panel._refresh_list()

        # Swap items: move row 1 up
        self.panel.list_widget.setCurrentRow(1)
        self.panel._on_up()

        sel = self.panel.get_selections()
        self.assertEqual(sel[0].edge_ref, "EdgeB")
        self.assertEqual(sel[0].order, 1)
        self.assertEqual(sel[1].edge_ref, "EdgeA")
        self.assertEqual(sel[1].order, 2)

        # Test P4 cancel
        self.panel._is_running = True
        self.panel._on_cancel()
        self.assertTrue(self.panel._cancel_requested)

    def test_stale_invalidation(self):
        """P2: Marking panel stale blocks calculate and flags items."""
        snap = RawBendLineSnapshot((0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 100.0)
        self.panel._selections = [BendSelectionItem(1, "EdgeA", "Unfold", snap, bend_id="BEND_01")]
        
        self.panel.mark_stale()
        self.assertTrue(self.panel._stale)
        self.assertIsNone(self.panel.get_selections()[0].bend_id)
        self.assertIn("перестроена", self.panel.status.text())


if __name__ == "__main__":
    unittest.main()
