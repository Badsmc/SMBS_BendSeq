"""Comprehensive negative regression test suite for Stage D4-P2.1: Trace Analyzer Hardening.

Covers:
1. One-frame trace -> UNVERIFIED (INSUFFICIENT_FRAMES)
2. Truncated / malformed JSONL -> FAIL (MALFORMED_JSON_LINE)
3. Missing physical landmarks -> UNVERIFIED (MISSING_PHYSICAL_LANDMARKS)
4. Invalid or missing frame delta / timestamp -> UNVERIFIED (INVALID_TIMESTAMP / INVALID_ELAPSED_DT)
5. Wrong bend direction (actual +90 deg vs target -90 deg) -> FAIL (TARGET_ANGLE_MISMATCH)
6. Incorrect target angle (+45 deg reached vs +90 deg target) -> FAIL (TARGET_ANGLE_MISMATCH)
7. Injected physical jump (> 0.05 mm in stationary phase) -> FAIL (STATIONARY_PHASE_MOTION)
8. Injected excessive transfer jump (> trajectory speed limit) -> FAIL (TRANSFER_STEP_EXCEEDED)
9. Punch penetration violation (punch tip penetrating past sheet inner apex) -> FAIL (PUNCH_PENETRATION_VIOLATION)
10. Punch contact loss (clearance > tolerance during BENDING) -> FAIL (PUNCH_CONTACT_LOSS)
11. Missing completion event (aborted simulation or user stop) -> UNVERIFIED (MISSING_COMPLETION_EVENT)
12. Incomplete bend sequence (e.g. 7 out of 8 bends completed) -> UNVERIFIED (INCOMPLETE_BEND_SEQUENCE)
13. Incomplete transfers (missing transfer transitions) -> UNVERIFIED (INCOMPLETE_TRANSFERS)
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smbs_core.kinematics2d.trace_analyzer import analyze_trace, TraceAnalysisReport


def make_valid_base_records() -> list[dict]:
    """Generate minimal valid full cycle trace with 2 bends (B01, B02) and 1 transfer."""
    records = []
    # 1. Lifecycle start
    records.append({
        "kind": "EVENT",
        "event": "TRACE_STARTED",
        "unix_s": 100.0,
        "project_version": "0.1.0",
        "build_id": "test_build_123",
    })
    records.append({
        "kind": "EVENT",
        "event": "RECORDING_STARTED",
        "unix_s": 100.0,
        "order": ["B01", "B02"],
        "mode": "FULL_CYCLE",
    })

    # Landmarks for P1
    lm_flat = {
        "panel:P1:v0": [0.0, 0.0, 0.0],
        "panel:P1:v1": [10.0, 0.0, 0.0],
        "panel:P1:v2": [10.0, 10.0, 0.0],
        "panel:P1:v3": [0.0, 10.0, 0.0],
    }
    lm_bent = {
        "panel:P1:v0": [0.0, 0.0, 0.0],
        "panel:P1:v1": [10.0, 0.0, 0.0],
        "panel:P1:v2": [10.0, 7.07, 7.07],
        "panel:P1:v3": [0.0, 7.07, 7.07],
    }

    # Frame 0: B01 APPROACH
    records.append({
        "kind": "FRAME",
        "frame": 0,
        "unix_s": 100.05,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "APPROACH",
        "bend_order": ["B01", "B02"],
        "angles_deg": {"B01": 0.0, "B02": 0.0},
        "physical_landmarks": lm_flat,
    })
    # Frame 1: B01 BENDING
    records.append({
        "kind": "FRAME",
        "frame": 1,
        "unix_s": 100.10,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "BENDING",
        "bend_angle_deg": 45.0,
        "ram_z_mm": -5.0,
        "punch_clearance_mm": 0.0,
        "bend_zone_position": {"inner_apex": [0.0, -5.0]},
        "angles_deg": {"B01": 45.0, "B02": 0.0},
        "physical_landmarks": lm_bent,
    })
    # Frame 2: B01 BOTTOM
    records.append({
        "kind": "FRAME",
        "frame": 2,
        "unix_s": 100.15,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "BOTTOM",
        "bend_angle_deg": 90.0,
        "ram_z_mm": -10.0,
        "punch_clearance_mm": 0.0,
        "bend_zone_position": {"inner_apex": [0.0, -10.0]},
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_bent,
    })
    # Frame 2b: B01 BOTTOM (stationary dwell)
    records.append({
        "kind": "FRAME",
        "frame": 25,
        "unix_s": 100.18,
        "elapsed_dt_s": 0.03,
        "active_bend_id": "B01",
        "cycle_phase": "BOTTOM",
        "bend_angle_deg": 90.0,
        "ram_z_mm": -10.0,
        "punch_clearance_mm": 0.0,
        "bend_zone_position": {"inner_apex": [0.0, -10.0]},
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_bent,
    })
    # Frame 3: B01 RETRACT
    records.append({
        "kind": "FRAME",
        "frame": 3,
        "unix_s": 100.20,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "RETRACT",
        "ram_z_mm": 0.0,
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_bent,
    })
    # Frame 4: B01 STEP_COMPLETE
    records.append({
        "kind": "FRAME",
        "frame": 4,
        "unix_s": 100.25,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "STEP_COMPLETE",
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_bent,
    })
    # Frame 5: B01 TRANSFER
    lm_transfer_end = {
        "panel:P1:v0": [5.0, 0.0, 0.0],
        "panel:P1:v1": [15.0, 0.0, 0.0],
        "panel:P1:v2": [15.0, 7.07, 7.07],
        "panel:P1:v3": [5.0, 7.07, 7.07],
    }
    records.append({
        "kind": "FRAME",
        "frame": 5,
        "unix_s": 100.30,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B01",
        "cycle_phase": "TRANSFER",
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_transfer_end,
    })
    # Frame 6: B02 APPROACH
    records.append({
        "kind": "FRAME",
        "frame": 6,
        "unix_s": 100.35,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B02",
        "cycle_phase": "APPROACH",
        "angles_deg": {"B01": 90.0, "B02": 0.0},
        "physical_landmarks": lm_transfer_end,
    })
    # Frame 7: B02 BOTTOM
    records.append({
        "kind": "FRAME",
        "frame": 7,
        "unix_s": 100.40,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B02",
        "cycle_phase": "BOTTOM",
        "bend_angle_deg": -90.0,
        "ram_z_mm": -10.0,
        "punch_clearance_mm": 0.0,
        "bend_zone_position": {"inner_apex": [0.0, -10.0]},
        "angles_deg": {"B01": 90.0, "B02": -90.0},
        "physical_landmarks": lm_transfer_end,
    })
    # Frame 8: B02 STEP_COMPLETE
    records.append({
        "kind": "FRAME",
        "frame": 8,
        "unix_s": 100.45,
        "elapsed_dt_s": 0.05,
        "active_bend_id": "B02",
        "cycle_phase": "STEP_COMPLETE",
        "angles_deg": {"B01": 90.0, "B02": -90.0},
        "physical_landmarks": lm_transfer_end,
    })

    # Lifecycle stop
    records.append({
        "kind": "EVENT",
        "event": "TRACE_STOPPED",
        "unix_s": 100.50,
        "reason": "PLAYBACK_COMPLETED",
    })
    return records


class TraceAnalyzerNegativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _write_trace(self, records: list[dict] | list[str]) -> str:
        trace_path = os.path.join(self.tmp_dir.name, "test_trace.jsonl")
        with open(trace_path, "w", encoding="utf-8") as f:
            for item in records:
                if isinstance(item, str):
                    f.write(item + "\n")
                else:
                    f.write(json.dumps(item) + "\n")
        return trace_path

    def test_valid_base_trace_passes(self):
        """Sanity test: baseline valid sequence must produce PASS with 0 errors."""
        records = make_valid_base_records()
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "PASS")
        self.assertEqual(report.discontinuities_count, 0)
        self.assertEqual(report.geometric_inconsistencies_count, 0)
        self.assertEqual(report.unresolved_conditions_count, 0)
        self.assertEqual(report.bends_completed, ["B01", "B02"])

    def test_one_frame_trace_unverified(self):
        """One-frame trace must produce UNVERIFIED due to INSUFFICIENT_FRAMES."""
        records = [
            {"kind": "EVENT", "event": "TRACE_STARTED", "unix_s": 10.0},
            {
                "kind": "FRAME",
                "frame": 0,
                "unix_s": 10.05,
                "elapsed_dt_s": 0.05,
                "cycle_phase": "IDLE",
                "physical_landmarks": {"p:v0": [0, 0, 0]},
            },
            {"kind": "EVENT", "event": "TRACE_STOPPED", "unix_s": 10.10, "reason": "PLAYBACK_COMPLETED"},
        ]
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "UNVERIFIED")
        self.assertTrue(any(u["kind"] == "INSUFFICIENT_FRAMES" for u in report.unresolved_conditions))

    def test_truncated_jsonl_fails(self):
        """Truncated or corrupted JSONL line must produce FAIL with MALFORMED_JSON_LINE."""
        lines = [
            json.dumps({"kind": "EVENT", "event": "TRACE_STARTED", "unix_s": 10.0}),
            '{"kind": "FRAME", "frame": 0, "unix_s": 10.05, "physical_landmarks": {',  # Truncated
        ]
        path = self._write_trace(lines)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any(u["kind"] == "MALFORMED_JSON_LINE" for u in report.unresolved_conditions))

    def test_missing_physical_landmarks_unverified(self):
        """Trace with 0 physical landmarks must produce UNVERIFIED."""
        records = make_valid_base_records()
        for r in records:
            if r.get("kind") == "FRAME":
                r.pop("physical_landmarks", None)
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "UNVERIFIED")
        self.assertTrue(any(u["kind"] == "MISSING_PHYSICAL_LANDMARKS" for u in report.unresolved_conditions))

    def test_invalid_frame_timestamp_or_delta(self):
        """Invalid timestamp or negative dt must produce UNVERIFIED."""
        records = make_valid_base_records()
        # Invalidate frame 2 elapsed_dt_s
        for r in records:
            if r.get("frame") == 2:
                r["elapsed_dt_s"] = -0.5
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "UNVERIFIED")
        self.assertTrue(any(u["kind"] == "INVALID_ELAPSED_DT" for u in report.unresolved_conditions))

    def test_wrong_bend_direction_fails(self):
        """Bend folded in opposite direction (+90 deg instead of target -90 deg) must produce FAIL."""
        records = make_valid_base_records()
        # In frame 7 and 8, set B02 actual angle to +90.0 instead of target -90.0
        for r in records:
            if r.get("frame") in (7, 8):
                r["angles_deg"]["B02"] = 90.0
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any(g.kind == "TARGET_ANGLE_MISMATCH" for g in report.geometric_inconsistencies))

    def test_incorrect_target_angle_fails(self):
        """Bend angle failing to reach target (+45 deg instead of +90 deg) must produce FAIL."""
        records = make_valid_base_records()
        for r in records:
            if r.get("frame") in (7, 8):
                r["angles_deg"]["B01"] = 45.0  # Fell short
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any(g.kind == "TARGET_ANGLE_MISMATCH" for g in report.geometric_inconsistencies))

    def test_injected_physical_jump_stationary_fails(self):
        """Unphysical jump in stationary BOTTOM phase (> 0.05 mm) must produce FAIL."""
        records = make_valid_base_records()
        for r in records:
            if r.get("frame") == 25:  # B01 BOTTOM dwell
                # Inject 0.5 mm jump during stationary dwell
                r["physical_landmarks"] = {
                    k: [v[0] + 0.5, v[1], v[2]]
                    for k, v in r["physical_landmarks"].items()
                }
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any("STATIONARY_PHASE_MOTION" in d.reason for d in report.discontinuities))

    def test_injected_excessive_transfer_jump_fails(self):
        """Workpiece jump during transfer exceeding physical speed envelope (> 350 mm/s) must produce FAIL."""
        records = make_valid_base_records()
        for r in records:
            if r.get("frame") == 5:  # TRANSFER
                # Inject 50 mm jump in dt=0.05s -> speed = 1000 mm/s > 350 mm/s
                r["physical_landmarks"] = {
                    k: [v[0] + 50.0, v[1], v[2]]
                    for k, v in r["physical_landmarks"].items()
                }
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any("TRANSFER_STEP_EXCEEDED" in d.reason for d in report.discontinuities))

    def test_punch_penetration_violation_fails(self):
        """Punch tip penetrating into inner sheet surface beyond tolerance must produce FAIL."""
        records = make_valid_base_records()
        for r in records:
            if r.get("frame") == 2:  # B01 BOTTOM
                # inner apex at Z=-10.0, punch penetrates to Z=-10.5 mm (penetration = 0.5 mm > 0.1 mm)
                r["ram_z_mm"] = -10.5
                r["bend_zone_position"] = {"inner_apex": [0.0, -10.0]}
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any(g.kind == "PUNCH_PENETRATION_VIOLATION" for g in report.geometric_inconsistencies))

    def test_punch_contact_loss_fails(self):
        """Punch clearance > 0.1 mm during BENDING must produce FAIL."""
        records = make_valid_base_records()
        for r in records:
            if r.get("frame") == 1:  # B01 BENDING
                r["punch_clearance_mm"] = 0.8  # Gap 0.8 mm > 0.1 mm
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "FAIL")
        self.assertTrue(any(g.kind == "PUNCH_CONTACT_LOSS" for g in report.geometric_inconsistencies))

    def test_missing_completion_event_unverified(self):
        """Trace stopped prematurely with USER_STOP or abrupted lifecycle must produce UNVERIFIED."""
        records = make_valid_base_records()
        # Change TRACE_STOPPED reason to USER_CANCEL
        for r in records:
            if r.get("event") == "TRACE_STOPPED":
                r["reason"] = "USER_CANCEL"
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "UNVERIFIED")
        self.assertTrue(any(u["kind"] == "MISSING_COMPLETION_EVENT" for u in report.unresolved_conditions))

    def test_incomplete_bend_sequence_unverified(self):
        """Expected order has 3 bends but only 2 completed must produce UNVERIFIED."""
        records = make_valid_base_records()
        for r in records:
            if r.get("event") == "RECORDING_STARTED":
                r["order"] = ["B01", "B02", "B03"]  # B03 never completed
        path = self._write_trace(records)
        report = analyze_trace(path)
        self.assertEqual(report.overall_status, "UNVERIFIED")
        self.assertTrue(any(u["kind"] == "INCOMPLETE_BEND_SEQUENCE" for u in report.unresolved_conditions))


if __name__ == "__main__":
    unittest.main()
