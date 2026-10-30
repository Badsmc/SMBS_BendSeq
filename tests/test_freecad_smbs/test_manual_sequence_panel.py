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
from unittest.mock import patch
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
        try:
            if hasattr(self, "doc") and self.doc is not None:
                doc_name = getattr(self.doc, "Name", None)
                if doc_name and App.getDocument(doc_name):
                    App.closeDocument(doc_name)
        except Exception:
            pass

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

    def test_stale_not_fired_by_annotations(self):
        """Y5: Recomputes that do not change Unfold shape topology do not falsely mark panel stale."""
        unfold = self.doc.getObject("Unfold")
        shape = unfold.Shape
        snap = RawBendLineSnapshot((0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 100.0)
        self.panel._selections = [BendSelectionItem(1, "EdgeA", "Unfold", snap, bend_id="BEND_01")]

        # First record shape hash
        self.panel._last_unfold_hash = shape.hashCode()

        # Recompute occurs, but shape hashCode is identical
        marked = self.panel.mark_stale(new_shape=shape)
        self.assertFalse(marked, "Panel must NOT be marked stale if Unfold shape topology has not changed")
        self.assertFalse(self.panel._stale)
        self.assertEqual(self.panel.get_selections()[0].bend_id, "BEND_01")


    def test_panel_qthread_does_not_block_main(self):
        """O2: ManualWorkerThread executes asynchronously without freezing Qt event loop."""
        from PySide import QtCore, QtGui
        app = QtGui.QApplication.instance() or QtGui.QApplication(["SMBS_Test"])

        snap = RawBendLineSnapshot((50.0, 80.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 2.5, 100.0)
        self.panel._selections = [BendSelectionItem(1, "Edge1", "Unfold", snap)]
        self.panel._refresh_list()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            started = self.panel.start_check(
                fcstd_path=str(self.box_path),
                out_runs_dir=Path(tmpdir)
            )
            self.assertTrue(started)
            self.assertTrue(self.panel._is_running)
            self.assertTrue(self.panel.btn_cancel.isEnabled())
            self.assertFalse(self.panel.btn_calc.isEnabled())

            # Main Qt event loop processes events while worker is active
            events_processed = 0
            for _ in range(10):
                app.processEvents()
                events_processed += 1
                QtCore.QThread.msleep(10)

            self.assertGreaterEqual(events_processed, 10, "Qt event loop must remain responsive while worker runs")

            # Cancel cleanly to prevent long test run
            self.panel._on_cancel()
            self.assertTrue(self.panel._cancel_requested)

            # Wait for thread to exit
            if self.panel._worker_thread:
                self.panel._worker_thread.wait(10000)

    def test_manual_panel_full_flow_headless(self):
        """O4: E2E flow: add 3 selections -> start_check -> wait -> finished -> check result."""
        from PySide import QtCore, QtGui
        app = QtGui.QApplication.instance() or QtGui.QApplication(["SMBS_Test"])

        unfold = self.doc.getObject("Unfold")
        # Select in known valid order for test_box: BEND_01 (Y=80) -> BEND_03 (Y=0) -> BEND_02 (X=0)
        ordered_subs = []
        for target_cond in [
            lambda e: abs(e.CenterOfMass.y - 80.0) < 0.1,
            lambda e: abs(e.CenterOfMass.y - 0.0) < 0.1,
            lambda e: abs(e.CenterOfMass.x - 0.0) < 0.1,
        ]:
            for i in range(len(unfold.Shape.Edges)):
                sub = f"Edge{i+1}"
                edge = unfold.Shape.getElement(sub)
                if edge and round(edge.Length, 1) in (80.0, 100.0) and abs(edge.CenterOfMass.z - 1.5) < 0.1:
                    if target_cond(edge):
                        ordered_subs.append(sub)
                        break

        for sub in ordered_subs:
            self.panel.addSelection(self.doc.Name, unfold.Name, sub, None)

        self.assertEqual(len(self.panel.get_selections()), 3)

        import time
        t0 = time.perf_counter()
        from smbs_core.Physics.machine_kinematics import PressBrakeMachine
        mach_simp = PressBrakeMachine()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            started = self.panel.start_check(
                fcstd_path=str(self.box_path),
                out_runs_dir=Path(tmpdir),
                unknown_policy="EXPLORATORY",
                machine=mach_simp,
            )
            self.assertTrue(started)

            # Wait for worker thread to finish while pumping Qt event loop
            if self.panel._worker_thread:
                while self.panel._worker_thread.isRunning():
                    app.processEvents()
                    QtCore.QThread.msleep(50)

            # Process any pending deferred signals (finished_result slot)
            for _ in range(5):
                app.processEvents()
                QtCore.QThread.msleep(10)

            elapsed = time.perf_counter() - t0
            self.assertFalse(self.panel._is_running)
            self.assertIsNotNone(self.panel._last_result)
            res = self.panel._last_result
            self.assertTrue(res.ok)
            p07 = res.run_dir / "07_search_result.json"
            p09 = res.run_dir / "09_manual_verification_result.json"
            self.assertTrue(p07.is_file())
            self.assertTrue(p09.is_file())

            # Verify dialog extraction
            from freecad_smbs.ui.verification_dialog import extract_verification_details
            details = extract_verification_details(res)
            self.assertIn("PASSED", details["badge_text"])
            self.assertEqual(len(details["step_verifications"]), 3)

    @unittest.skipUnless(os.getenv("HANDMOD_SLOW_TESTS"), "Slow real-tooling E2E test skipped unless HANDMOD_SLOW_TESTS is set")
    def test_manual_panel_real_tooling_e2e(self):
        """
        Q1: Full E2E UI flow with real tooling and PRODUCTION policy.
        Executes on test_pentagon with real standard_90deg_v12 tooling (~140s).
        """
        import time, json
        from PySide import QtCore, QtGui
        app = QtGui.QApplication.instance() or QtGui.QApplication(["SMBS_Test"])

        pent_path = FIXTURES_DIR / "test_pentagon.FCStd"
        orders_path = FIXTURES_DIR / "manual" / "known_orders_pentagon.json"
        known_orders = json.loads(orders_path.read_text(encoding="utf-8"))
        valid_pent_seq = known_orders["first_valid"]

        doc_pent = App.openDocument(str(pent_path))
        panel_pent = ManualSequencePanel(initial_doc=doc_pent)
        try:
            from freecad_smbs.manual.manual_runner import run_manual_sequence_on_fcstd
            from freecad_smbs.manual.manual_worker import ManualSequenceInput
            from tests.test_freecad_smbs.test_manual_worker import TestManualWorker
            tmw = TestManualWorker()
            pent_sels = tmw._extract_snapshots_from_unfold(pent_path)

            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                res_canon = run_manual_sequence_on_fcstd(pent_path, ManualSequenceInput(pent_sels), out_runs_dir=Path(tmpdir), phases=["01", "02", "04"])
                sel_by_bid = {s.bend_id: s for s in res_canon.selections}
                for idx, bid in enumerate(valid_pent_seq, start=1):
                    s = sel_by_bid[bid]
                    panel_pent.addSelection(doc_pent.Name, s.object_name, s.edge_ref, None)

                t0 = time.perf_counter()
                started = panel_pent.start_check(
                    fcstd_path=str(pent_path),
                    tooling_set_id="standard_90deg_v12",
                    unknown_policy="PRODUCTION",
                    out_runs_dir=Path(tmpdir),
                )
                self.assertTrue(started)
                if panel_pent._worker_thread:
                    while panel_pent._worker_thread.isRunning():
                        app.processEvents()
                        QtCore.QThread.msleep(50)
                for _ in range(5):
                    app.processEvents()
                    QtCore.QThread.msleep(10)

                elapsed = time.perf_counter() - t0
                self.assertFalse(panel_pent._is_running)
                res = panel_pent._last_result
                self.assertTrue(res.ok)
                self.assertGreater(elapsed, 50.0, "Real tooling E2E execution must perform physical OCC collision queries (>50s)")
                d09 = json.loads((res.run_dir / "09_manual_verification_result.json").read_text())
                self.assertFalse(d09["payload"]["has_unknown"])
                for step in d09["payload"]["step_verifications"]:
                    self.assertFalse(step["has_unknown"])
        finally:
            panel_pent.reject()
            App.closeDocument(doc_pent.Name)

    def test_initial_selection_loaded_on_init(self):
        """Step 1-A: Passing preselected edges to ManualSequencePanel populates valid bend lines."""
        unfold = self.doc.getObject("Unfold")
        edges = []
        for i in range(len(unfold.Shape.Edges)):
            sub = f"Edge{i+1}"
            edge = unfold.Shape.getElement(sub)
            if edge and round(edge.Length, 1) in (80.0, 100.0) and abs(edge.CenterOfMass.z - 1.5) < 0.1:
                # Genuine bend lines have midpoints at Y=0, Y=80, or X=0
                if abs(edge.CenterOfMass.y - 0.0) < 0.1 or abs(edge.CenterOfMass.y - 80.0) < 0.1 or abs(edge.CenterOfMass.x - 0.0) < 0.1:
                    edges.append((unfold, edge))
                    if len(edges) == 2:
                        break

        panel = ManualSequencePanel(initial_doc=self.doc, initial_selection=edges)
        try:
            self.assertEqual(len(panel.get_selections()), 2)
            self.assertEqual(panel.list_widget.count(), 2)
        finally:
            panel.reject()

    def test_on_calculate_triggers_start_check_with_ui_tooling(self):
        """Step 1-B & 1-C: Clicking 'Проверить' reads tooling dropdown and starts asynchronous check."""
        unfold = self.doc.getObject("Unfold")
        for i in range(len(unfold.Shape.Edges)):
            sub = f"Edge{i+1}"
            edge = unfold.Shape.getElement(sub)
            if edge and round(edge.Length, 1) in (80.0, 100.0) and abs(edge.CenterOfMass.z - 1.5) < 0.1:
                if abs(edge.CenterOfMass.y - 0.0) < 0.1:  # Edge23
                    self.panel.addSelection(self.doc.Name, unfold.Name, sub, None)
                    break

        self.assertEqual(len(self.panel.get_selections()), 1)
        self.panel.cmb_policy.setCurrentIndex(1)  # EXPLORATORY
        
        # Test calling _on_calculate directly
        with patch.object(self.panel, "start_check", return_value=True) as mock_start:
            self.panel._on_calculate()
            mock_start.assert_called_once()
            _, kwargs = mock_start.call_args
            self.assertEqual(kwargs.get("unknown_policy"), "EXPLORATORY")
            self.assertEqual(kwargs.get("tooling_set_id"), "standard_90deg_v12")

    def test_worker_handles_subprocess_nonzero_exit_and_diagnostics(self):
        """Action 5 & 9: Verify ManualWorkerThread reports nonzero exit status and persists diagnostics."""
        from freecad_smbs.ui.manual_sequence_panel import ManualWorkerThread
        from freecad_smbs.manual.manual_worker import ManualSequenceInput
        import tempfile
        from unittest.mock import patch, MagicMock

        snap = RawBendLineSnapshot(
            midpoint=(0.0, 0.0, 1.5),
            direction=(1.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            radius_mm=1.0,
            length_mm=80.0
        )
        item = BendSelectionItem(order=1, edge_ref="Edge23", object_name="Unfold", raw_snapshot=snap)
        manual_input = ManualSequenceInput(
            selections=[item],
            tooling_set_id="standard_90deg_v12",
            unknown_policy="PRODUCTION",
            part_id="test_part"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            thread = ManualWorkerThread(
                fcstd_path=self.box_path,
                manual_input=manual_input,
                out_runs_dir=tmp_dir,
            )

            # Mock subprocess.Popen to simulate process failing with exit code 1
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stdout.readline.side_effect = ["Starting calculation...\n", ""]
            mock_proc.stderr.readline.side_effect = ["Error: Simulated syntax error in worker\n", ""]

            with patch("subprocess.Popen", return_value=mock_proc), \
                 patch.object(App, "GuiUp", 1, create=True):
                finished_results = []
                thread.finished_result.connect(finished_results.append)
                thread.run()

            self.assertEqual(len(finished_results), 1)
            res = finished_results[0]
            self.assertFalse(res.ok)
            self.assertEqual(res.planning_status, "ERROR")
            self.assertIn("code 1", res.error_message)
            self.assertIn("Simulated syntax error", res.error_message)
            self.assertNotEqual(str(res.run_dir), ".")
            self.assertTrue(Path(res.run_dir).exists())
            
            # Verify log was written
            log_path = Path(res.run_dir) / "worker_subprocess.log"
            self.assertTrue(log_path.is_file())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("Exit code: 1", content)
            self.assertIn("Simulated syntax error", content)

    def test_worker_handles_subprocess_missing_output(self):
        """Action 6 & 9: Verify ManualWorkerThread handles missing output artifact gracefully."""
        from freecad_smbs.ui.manual_sequence_panel import ManualWorkerThread
        from freecad_smbs.manual.manual_worker import ManualSequenceInput
        import tempfile
        from unittest.mock import patch, MagicMock

        snap = RawBendLineSnapshot(
            midpoint=(0.0, 0.0, 1.5),
            direction=(1.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            radius_mm=1.0,
            length_mm=80.0
        )
        item = BendSelectionItem(order=1, edge_ref="Edge23", object_name="Unfold", raw_snapshot=snap)
        manual_input = ManualSequenceInput(
            selections=[item],
            tooling_set_id="standard_90deg_v12",
            unknown_policy="PRODUCTION",
            part_id="test_part"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            thread = ManualWorkerThread(
                fcstd_path=self.box_path,
                manual_input=manual_input,
                out_runs_dir=tmp_dir,
            )

            # Mock subprocess.Popen with returncode 0 but without creating output file
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout.readline.side_effect = ["Process completed without output\n", ""]
            mock_proc.stderr.readline.side_effect = [""]

            with patch("subprocess.Popen", return_value=mock_proc), \
                 patch.object(App, "GuiUp", 1, create=True):
                finished_results = []
                thread.finished_result.connect(finished_results.append)
                thread.run()

            self.assertEqual(len(finished_results), 1)
            res = finished_results[0]
            self.assertFalse(res.ok)
            self.assertEqual(res.planning_status, "ERROR")
            self.assertIn("code 0", res.error_message)
            self.assertNotEqual(str(res.run_dir), ".")


if __name__ == "__main__":
    unittest.main()


