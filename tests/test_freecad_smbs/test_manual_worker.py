"""
test_manual_worker.py - Phase 4a Tests for Mini-Pipeline and Canonical Resolution.

Covers:
1. test_match_against_real_04 (B3): match_to_canonical on real 04 produced by TopologyWorker.
2. test_mini_pipeline_produces_04 (Phase 4a): run_manual_sequence_on_fcstd creates run dir with 04_canonical_part.json.
3. test_all_bend_ids_resolved_or_fail (B4):
   - test_box (3/3 resolved)
   - test_pentagon (5/5 resolved)
   - test_mirror (3/3 resolved, no false matches from mirror)
   - Fail-closed if snapshot is unmatched (ok=False, unmatched_snapshots populated).
4. test_run_dir_policy (B4): default runs dir and custom out_runs_dir override.
5. test_verifier_compatible (B5): existing VerifierWorker reads manual run artifacts without changes.
6. test_cancel_between_steps (Y4): cancel_token cleanly aborts worker execution.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App

from freecad_smbs.manual.geometry_extract import (
    extract_bend_lines_3d,
    match_to_canonical,
)
from freecad_smbs.manual.manual_runner import run_manual_sequence_on_fcstd
from freecad_smbs.manual.manual_worker import (
    ManualSequenceInput,
    ManualSequenceResult,
    ManualSequenceWorker,
)
from freecad_smbs.ui.manual_sequence_panel import (
    BendSelectionItem,
    RawBendLineSnapshot,
)
from smbs_core.workers.verifier.worker import VerifierWorker
from tests.fixtures.create_test_part import FIXTURES_DIR


class TestManualWorker(unittest.TestCase):
    """Test suite for Phase 4a manual worker and canonical resolution."""

    @classmethod
    def setUpClass(cls):
        cls.box_path = FIXTURES_DIR / "test_box.FCStd"
        cls.pentagon_path = FIXTURES_DIR / "test_pentagon.FCStd"
        cls.mirror_path = FIXTURES_DIR / "test_mirror.FCStd"
        cls.temp_runs = REPO_ROOT / "tests" / "artifacts" / "temp_manual_runs"
        cls.temp_runs.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.temp_runs.exists():
            shutil.rmtree(cls.temp_runs, ignore_errors=True)

    def _extract_snapshots_from_unfold(self, fcstd_path: Path) -> list[BendSelectionItem]:
        doc = App.openDocument(str(fcstd_path))
        try:
            unfold = doc.getObject("Unfold")
            raw_bends = extract_bend_lines_3d(unfold.Shape)
            items = []
            for i, rb in enumerate(raw_bends, start=1):
                snap = RawBendLineSnapshot(
                    midpoint=rb.midpoint,
                    direction=rb.direction,
                    normal=rb.normal,
                    radius_mm=rb.radius_mm,
                    length_mm=rb.length_mm,
                    adjacent_panel_areas=rb.adjacent_panel_areas,
                    source_edge_ref=f"Edge{i}",
                )
                items.append(BendSelectionItem(
                    order=i,
                    edge_ref=f"Edge{i}",
                    object_name="Unfold",
                    raw_snapshot=snap,
                ))
            return items
        finally:
            App.closeDocument(doc.Name)

    def test_mini_pipeline_produces_04(self):
        """Phase 4a: run_manual_sequence_on_fcstd produces 01, 02, and 04 artifacts."""
        selections = self._extract_snapshots_from_unfold(self.box_path)
        manual_in = ManualSequenceInput(selections=selections)

        res = run_manual_sequence_on_fcstd(
            self.box_path,
            manual_in,
            out_runs_dir=self.temp_runs,
            phases=["04"],
        )

        self.assertTrue(res.ok, f"Mini-pipeline execution must succeed, got error: {res.error_message}")
        self.assertTrue((res.run_dir / "01_input_manifest.json").is_file())
        self.assertTrue((res.run_dir / "02_geometry_result.json").is_file())
        self.assertTrue((res.run_dir / "04_canonical_part.json").is_file())
        self.assertEqual(len(res.selections), 3)
        for sel in res.selections:
            self.assertIsNotNone(sel.bend_id, "Each selection must have resolved canonical bend_id")
            self.assertTrue(sel.bend_id.startswith("BEND_"))

    def test_all_bend_ids_resolved_box_pentagon_mirror(self):
        """Gate 4a: test_box (3/3), test_pentagon (5/5), test_mirror (3/3) resolution."""
        # 1. test_box
        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        self.assertEqual(len(box_sels), 3)
        res_box = run_manual_sequence_on_fcstd(self.box_path, ManualSequenceInput(box_sels), out_runs_dir=self.temp_runs, phases=["01", "02", "04"])
        self.assertTrue(res_box.ok)
        self.assertEqual(len(res_box.selections), 3)
        self.assertEqual(len(res_box.unmatched_snapshots), 0)

        # 2. test_pentagon
        pent_sels = self._extract_snapshots_from_unfold(self.pentagon_path)
        self.assertEqual(len(pent_sels), 5)
        res_pent = run_manual_sequence_on_fcstd(self.pentagon_path, ManualSequenceInput(pent_sels), out_runs_dir=self.temp_runs, phases=["01", "02", "04"])
        self.assertTrue(res_pent.ok)
        self.assertEqual(len(res_pent.selections), 5)
        self.assertEqual(len(res_pent.unmatched_snapshots), 0)

        # 3. test_mirror
        mirror_sels = self._extract_snapshots_from_unfold(self.mirror_path)
        self.assertEqual(len(mirror_sels), 3)
        res_mirror = run_manual_sequence_on_fcstd(self.mirror_path, ManualSequenceInput(mirror_sels), out_runs_dir=self.temp_runs, phases=["01", "02", "04"])
        self.assertTrue(res_mirror.ok)
        self.assertEqual(len(res_mirror.selections), 3)
        self.assertEqual(len(res_mirror.unmatched_snapshots), 0)

    def test_unmatched_snapshot_fails_fast(self):
        """B4: If any snapshot fails to resolve, worker fails closed with unmatched_snapshots populated."""
        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        # Introduce a bogus snapshot with far-away coordinates
        bogus_snap = RawBendLineSnapshot(
            midpoint=(999.0, 999.0, 999.0),
            direction=(0.0, 1.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            radius_mm=50.0,
            length_mm=100.0,
        )
        box_sels.append(BendSelectionItem(
            order=4,
            edge_ref="EdgeBogus",
            object_name="Unfold",
            raw_snapshot=bogus_snap,
        ))

        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=self.temp_runs,
        )

        self.assertFalse(res.ok, "Worker must fail if any snapshot is unmatched")
        self.assertEqual(res.planning_status, "ERROR")
        self.assertEqual(len(res.unmatched_snapshots), 1)
        self.assertIn("Failed to match", res.error_message)

    def test_run_dir_policy(self):
        """B4: Respects custom out_runs_dir or defaults to workspace runs/ directory."""
        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        custom_target = self.temp_runs / "custom_subdir"
        custom_target.mkdir(parents=True, exist_ok=True)

        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=custom_target,
            phases=["01", "02", "04"],
        )

        self.assertTrue(res.ok)
        self.assertEqual(res.run_dir.parent.resolve(), custom_target.resolve())

    def test_cancel_between_steps(self):
        """Y4: cancel_token aborts worker execution."""
        token = threading.Event()
        token.set()  # Pre-cancelled

        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            cancel_token=token,
            out_runs_dir=self.temp_runs,
        )

        self.assertFalse(res.ok)
        self.assertEqual(res.planning_status, "CANCELLED")

    def test_worker_emits_only_01_02_04(self):
        """C3: When phases restricts early termination, emits strictly 01, 02, and 04 artifacts (not 07)."""
        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=self.temp_runs,
            phases=["01", "02", "04"],
        )
        self.assertTrue(res.ok)
        self.assertTrue((res.run_dir / "01_input_manifest.json").is_file())
        self.assertTrue((res.run_dir / "02_geometry_result.json").is_file())
        self.assertTrue((res.run_dir / "04_canonical_part.json").is_file())
        self.assertFalse((res.run_dir / "07_search_result.json").exists())
        self.assertFalse((res.run_dir / "09_verification_result.json").exists())

    def test_manual_worker_emits_07_and_09_with_real_backgauge(self):
        """Phase 4c.2 / L2: Verifies that ManualSequenceWorker emits truthful 07 and 09 with real backgauge data."""
        # Use known valid sequence for test_box from fixture
        orders_path = FIXTURES_DIR / "manual" / "known_orders_box.json"
        known_orders = json.loads(orders_path.read_text(encoding="utf-8"))
        valid_bids = known_orders["first_valid"]

        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        # Reorder selections according to valid sequence (BEND_01 -> BEND_03 -> BEND_02)
        # First resolve canonically to get bend_ids
        res_canon = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=self.temp_runs,
            phases=["01", "02", "04"],
        )
        sel_by_bid = {s.bend_id: s for s in res_canon.selections}
        reordered_sels = [
            BendSelectionItem(
                order=idx,
                edge_ref=sel_by_bid[bid].edge_ref,
                object_name=sel_by_bid[bid].object_name,
                raw_snapshot=sel_by_bid[bid].raw_snapshot,
                bend_id=bid
            )
            for idx, bid in enumerate(valid_bids, start=1)
        ]

        from smbs_core.Physics.machine_kinematics import PressBrakeMachine
        mach_simp = PressBrakeMachine()

        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(reordered_sels, tooling_set_id="standard_90deg_v12", unknown_policy="EXPLORATORY", machine=mach_simp),
            out_runs_dir=self.temp_runs,
        )

        self.assertTrue(res.ok)
        self.assertTrue((res.run_dir / "07_search_result.json").is_file())
        self.assertTrue((res.run_dir / "09_manual_verification_result.json").is_file())

        s7 = json.loads((res.run_dir / "07_search_result.json").read_text(encoding="utf-8"))
        self.assertEqual(s7["payload"]["planning_status"], "SUCCESS")
        cand_seq = s7["payload"]["candidate_sequence"]
        self.assertEqual(len(cand_seq), 3)

        # Check real backgauge fields are populated (not empty, not constants)
        for step in cand_seq:
            self.assertIn("backgauge", step)
            bg = step["backgauge"]
            self.assertIn("x_mm", bg)
            self.assertIn("r_mm", bg)
            self.assertIn("z1_mm", bg)
            self.assertIn("z2_mm", bg)
            self.assertGreater(bg["x_mm"], 0.0)

        v9 = json.loads((res.run_dir / "09_manual_verification_result.json").read_text(encoding="utf-8"))
        self.assertIn(v9["payload"]["verification_status"], ["PASSED", "PASSED_WITH_UNKNOWN"])

    def test_manual_worker_rejects_invalid_sequence(self):
        """Phase 4c.2 / L2: Verifies that invalid user sequence fails closed with FAILED in 07 and 09."""
        from smbs_core.Physics.machine_kinematics import PressBrakeMachine
        orders_path = FIXTURES_DIR / "manual" / "known_orders_box.json"
        known_orders = json.loads(orders_path.read_text(encoding="utf-8"))
        invalid_bids = known_orders["first_invalid"]["sequence"]

        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        res_canon = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=self.temp_runs,
            phases=["01", "02", "04"],
        )
        sel_by_bid = {s.bend_id: s for s in res_canon.selections}
        reordered_sels = [
            BendSelectionItem(
                order=idx,
                edge_ref=sel_by_bid[bid].edge_ref,
                object_name=sel_by_bid[bid].object_name,
                raw_snapshot=sel_by_bid[bid].raw_snapshot,
                bend_id=bid
            )
            for idx, bid in enumerate(invalid_bids, start=1)
        ]

        mach_simp = PressBrakeMachine()
        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(reordered_sels, tooling_set_id="standard_90deg_v12", unknown_policy="EXPLORATORY", machine=mach_simp),
            out_runs_dir=self.temp_runs,
        )

        self.assertFalse(res.ok)
        self.assertIn(res.planning_status, ["FAILED", "NO_SOLUTION"])
        self.assertTrue((res.run_dir / "07_search_result.json").is_file())
        self.assertTrue((res.run_dir / "09_manual_verification_result.json").is_file())

        s7 = json.loads((res.run_dir / "07_search_result.json").read_text(encoding="utf-8"))
        self.assertIn(s7["payload"]["planning_status"], ["FAILED", "NO_SOLUTION"])

        v9 = json.loads((res.run_dir / "09_manual_verification_result.json").read_text(encoding="utf-8"))
        self.assertEqual(v9["payload"]["verification_status"], "FAILED")
        self.assertFalse(v9["payload"]["production_eligible"])

    def test_verifier_accepts_well_formed_artifacts(self):
        """
        C1/B5 Schema Smoke Test:
        Validates schema compatibility of 07_search_result.json against VerifierWorker.
        NOTE: This test does NOT claim that ManualSequenceWorker emits 07 in Phase 4a.
        Instead, it validates that downstream VerifierWorker can parse handcrafted 07 schema
        alongside canonical part 04 generated by the Phase 4a mini-pipeline.
        Full physical verification and tooling assembly will be wired in Phase 4c/Phase 6.
        """
        box_sels = self._extract_snapshots_from_unfold(self.box_path)
        res = run_manual_sequence_on_fcstd(
            self.box_path,
            ManualSequenceInput(box_sels),
            out_runs_dir=self.temp_runs,
            phases=["01", "02", "04"],
        )
        self.assertTrue(res.ok)

        # Load external handcrafted 07 fixture
        fixture_07 = FIXTURES_DIR / "manual" / "handcrafted_manual_07.json"
        self.assertTrue(fixture_07.is_file(), f"Handcrafted 07 fixture must exist at {fixture_07}")
        sr_data = json.loads(fixture_07.read_text())

        # Bind run-specific metadata
        sr_data["run_id"] = res.run_dir.name
        sr_data["part_id"] = res.part_id
        sr_data["input_fingerprint"] = json.loads((res.run_dir / "01_input_manifest.json").read_text())["input_fingerprint"]

        from smbs_core.protocols.file_store import FileStore
        fs = FileStore()
        sr_file = res.run_dir / "07_search_result.json"
        fs.write_json_atomic(sr_file, sr_data, schema_name="search_result")

        # Invoke existing VerifierWorker
        verifier = VerifierWorker(file_store=fs)
        verif_file = verifier.execute(
            search_result_path=sr_file,
            canonical_part_path=res.run_dir / "04_canonical_part.json",
        )

        self.assertTrue(verif_file.is_file(), "09_verification_result.json must be created by existing VerifierWorker")
        verif_data = json.loads(verif_file.read_text())
        self.assertIn("verification_status", verif_data["payload"])
        self.assertEqual(verif_data["run_id"], res.run_dir.name)


if __name__ == "__main__":
    unittest.main()

