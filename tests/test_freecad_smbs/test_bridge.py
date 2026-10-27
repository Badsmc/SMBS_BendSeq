"""
Unit tests for smbs_bridge (Commit C3).
Tests read-only guarantee, status presentation, and Q5 honesty badge logic.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from freecad_smbs.integration.smbs_bridge import (
    load_run,
    present_info,
    present_verification,
)
from freecad_smbs.ui.badges import (
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
)
from smbs_core.viewer.run_loader import RunArtifacts


class TestSMBSBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.run_path = Path(self.temp_dir.name)

        # Create minimal valid run files for testing
        self.geom_file = self.run_path / "geometry.brep"
        self.geom_file.write_text("DUMMY_BREP_GEOMETRY", encoding="utf-8")

        self.cp_file = self.run_path / "04_canonical_part.json"
        self.cp_file.write_text(
            json.dumps({
                "schema_name": "canonical_part",
                "payload": {
                    "bends": {
                        "B01": {
                            "bend_line_flat_pattern": {
                                "start_mm": [0.0, 5.0, 0.0],
                                "end_mm": [10.0, 5.0, 0.0],
                            }
                        },
                        "B02": {
                            "bend_line_flat_pattern": {
                                "start_mm": [0.0, 15.0, 0.0],
                                "end_mm": [10.0, 15.0, 0.0],
                            }
                        },
                    },
                    "geometry_artifact": {
                        "file_path": "geometry.brep",
                        "format": "BREP",
                        "content_hash": "a" * 64,
                    },
                },
            }),
            encoding="utf-8",
        )

        self.sr_file = self.run_path / "07_search_result.json"
        self.sr_file.write_text(
            json.dumps({
                "schema_name": "search_result",
                "payload": {
                    "candidate_sequence": [
                        {"step": 1, "bend_id": "B01", "planner_status": "VALID"},
                        {"step": 2, "bend_id": "B02", "planner_status": "VALID"},
                    ]
                },
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bridge_readonly(self):
        """C3-3: Verify smbs_bridge never mutates files and only reads in read-only mode."""
        import hashlib

        def dir_hashes(path: Path) -> dict:
            hashes = {}
            for f in sorted(path.glob("*")):
                if f.is_file():
                    hashes[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
            return hashes

        # 1. Capture exact file hashes before bridge calls
        pre_hashes = dir_hashes(self.run_path)

        opened_modes = []
        orig_path_open = Path.open
        orig_builtins_open = open

        def tracked_path_open(path_obj, *args, **kwargs):
            mode = kwargs.get("mode", args[0] if args else "r")
            opened_modes.append((str(path_obj), mode))
            if any(m in mode for m in ("w", "a", "+", "x")):
                raise PermissionError(f"Attempted write-mode Path.open({path_obj}, {mode}) in read-only bridge!")
            return orig_path_open(path_obj, *args, **kwargs)

        def tracked_builtin_open(file, *args, **kwargs):
            mode = kwargs.get("mode", args[0] if args else "r")
            opened_modes.append((str(file), mode))
            if any(m in mode for m in ("w", "a", "+", "x")):
                raise PermissionError(f"Attempted write-mode open({file}, {mode}) in read-only bridge!")
            return orig_builtins_open(file, *args, **kwargs)

        with patch.object(Path, "open", tracked_path_open), patch("builtins.open", tracked_builtin_open):
            artifacts = load_run(self.run_path)
            self.assertIsInstance(artifacts, RunArtifacts)

            # Exercise presentation methods
            info = present_info(artifacts)
            self.assertIn("run_dir", info)

            verif = present_verification(artifacts)
            self.assertIn("status", verif)

        # 2. All recorded file opens must be strictly read-only
        self.assertTrue(len(opened_modes) > 0, "Expected file read operations to be recorded")
        for file_path, mode in opened_modes:
            self.assertIn(mode, ("r", "rb"))
            self.assertNotIn("w", mode)
            self.assertNotIn("a", mode)

        # 3. Post-execution file hashes must be bit-identical (zero mutation)
        post_hashes = dir_hashes(self.run_path)
        self.assertEqual(pre_hashes, post_hashes, "Files in run_dir were mutated during bridge execution!")

    def test_unverified_badge_logic_none(self):
        """Q5: Missing 09_verification_result.json -> UNVERIFIED (gray)."""
        artifacts = load_run(self.run_path)
        self.assertIsNone(artifacts.verification_result)

        verif = present_verification(artifacts)
        self.assertEqual(verif["status"], BADGE_UNVERIFIED)
        self.assertIsNone(verif["production_eligible"])
        self.assertEqual(verif["color_rgba"], (0.5, 0.5, 0.5, 1.0))
        self.assertEqual(verif["color_hex"], "#757575")

    def test_unverified_badge_logic_eligible_false(self):
        """Q5: 09_verification_result.json with production_eligible=False -> VERIFICATION_FAILED (orange)."""
        vr_file = self.run_path / "09_verification_result.json"
        vr_file.write_text(
            json.dumps({
                "schema_name": "verification_result",
                "payload": {
                    "production_eligible": False,
                    "reasons": ["Tooling collision detected on bend B02"],
                },
            }),
            encoding="utf-8",
        )

        artifacts = load_run(self.run_path)
        verif = present_verification(artifacts)
        self.assertEqual(verif["status"], BADGE_VERIFICATION_FAILED)
        self.assertFalse(verif["production_eligible"])
        self.assertEqual(verif["color_rgba"], (1.0, 0.5, 0.0, 1.0))
        self.assertEqual(verif["color_hex"], "#EF6C00")
        self.assertIn("Tooling collision", verif["reasons"][0])

    def test_unverified_badge_logic_eligible_true(self):
        """Q5: 09_verification_result.json with production_eligible=True -> VERIFIED (green)."""
        vr_file = self.run_path / "09_verification_result.json"
        vr_file.write_text(
            json.dumps({
                "schema_name": "verification_result",
                "payload": {"production_eligible": True, "reasons": []},
            }),
            encoding="utf-8",
        )

        artifacts = load_run(self.run_path)
        verif = present_verification(artifacts)
        self.assertEqual(verif["status"], BADGE_VERIFIED)
        self.assertTrue(verif["production_eligible"])
        self.assertEqual(verif["color_rgba"], (0.0, 0.6, 0.0, 1.0))
        self.assertEqual(verif["color_hex"], "#2E7D32")

    def test_present_verification_kl26_fail_fast(self):
        """KL-26: verification_status='VERIFIED' ∧ production_eligible=False must raise ValueError."""
        vr_file = self.run_path / "09_verification_result.json"
        vr_file.write_text(
            json.dumps({
                "schema_name": "verification_result",
                "payload": {
                    "verification_status": "VERIFIED",
                    "production_eligible": False,
                },
            }),
            encoding="utf-8",
        )
        artifacts = load_run(self.run_path)
        with self.assertRaises(ValueError) as ctx:
            present_verification(artifacts)
        self.assertIn("KL-26", str(ctx.exception))

    def test_present_info_content(self):
        """Verify present_info reports correct step counts and honesty limitations."""
        artifacts = load_run(self.run_path)
        info = present_info(artifacts)
        self.assertEqual(info["step_count"], 2)
        self.assertEqual(info["bend_ids"], ["B01", "B02"])
        # Check that honesty limitations include KL-12 and KL-16
        limitations_str = " ".join(info["limitations"])
        self.assertIn("KL-12", limitations_str)
        self.assertIn("KL-16", limitations_str)

    def test_bridge_get_annotations(self):
        """C4: Verify smbs_bridge.get_annotations computes BendAnnotation list."""
        from unittest.mock import MagicMock, patch
        from freecad_smbs.integration.smbs_bridge import get_annotations

        mock_graphs = MagicMock()
        mock_graphs.direction_by_bend = {"B01": "up", "B02": "down"}

        with patch("freecad_smbs.integration.smbs_bridge.build_graphs", return_value=mock_graphs):
            artifacts = load_run(self.run_path)
            anns = get_annotations(artifacts)

        self.assertEqual(len(anns), 2)
        self.assertEqual(anns[0].step, 1)
        self.assertEqual(anns[0].bend_id, "B01")
        self.assertEqual(anns[0].midpoint, (5.0, 5.0, 0.0))  # (start + end) / 2
        self.assertEqual(anns[0].ui_direction, "up")
        self.assertEqual(anns[0].verification, "UNVERIFIED")

        self.assertEqual(anns[1].step, 2)
        self.assertEqual(anns[1].bend_id, "B02")
        self.assertEqual(anns[1].midpoint, (5.0, 15.0, 0.0))
        self.assertEqual(anns[1].ui_direction, "down")

        info = present_info(artifacts)
        self.assertEqual(info["step_count"], 2)
