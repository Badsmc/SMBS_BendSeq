"""
test_f3b_kl16_kl28.py - Tests for F.3b punch descent, KL-16 machine frame honesty, and KL-28 sidecars.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.collision_detector import CollisionStatus
from smbs_core.models.tooling import (
    DatumRole,
    GeometryStatus,
    ToolingKind,
)
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend


class TestKL28Sidecars(unittest.TestCase):
    """Verify Backgauge and Clamp sidecar datums (KL-28)."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.toollib = self.repo_root / "toollibrary"

    def test_backgauge_sidecar_present_and_valid(self):
        """toollibrary/Backgauge/Backgauge.tooling.json must exist with verified datums."""
        bg_json = self.toollib / "Backgauge" / "Backgauge.tooling.json"
        self.assertTrue(bg_json.is_file(), f"Missing sidecar {bg_json}")

        data = json.loads(bg_json.read_text(encoding="utf-8"))
        self.assertEqual(data.get("kind"), "backgauge")
        datums = data.get("datums", {})
        self.assertIn("mounting_plane", datums)
        self.assertIn("working_axis", datums)
        self.assertIn("stop_face", datums)

    def test_clamp_sidecar_present_and_valid(self):
        """toollibrary/Clamp/Clamp.tooling.json must exist with verified datums."""
        clamp_json = self.toollib / "Clamp" / "Clamp.tooling.json"
        self.assertTrue(clamp_json.is_file(), f"Missing sidecar {clamp_json}")

        data = json.loads(clamp_json.read_text(encoding="utf-8"))
        self.assertEqual(data.get("kind"), "clamp")
        datums = data.get("datums", {})
        self.assertIn("mounting_plane", datums)
        self.assertIn("working_axis", datums)


class TestKL16MachineFrameHonesty(unittest.TestCase):
    """Verify KL-16 honest UNKNOWN when machine_frame is missing, and real shape when present."""

    def test_machine_frame_missing_honest_unknown(self):
        """When machine_frame is not supplied, assembly has no machine frame and collision is UNKNOWN."""
        backend = PythonOCCBackend()
        machine = PressBrakeMachine.with_default_tooling()
        assembly = machine.setup_assembly(backend)

        # Machine frame must be None (no fabricated geometry)
        self.assertIsNone(assembly.machine_frame)
        self.assertIsNone(assembly.get_world_shape(ToolingKind.MACHINE_FRAME))

        from smbs_core.Physics.collision_detector import CollisionDetector
        detector = CollisionDetector(assembly, backend)
        fake_shape = backend.make_box(10.0, 10.0, 1.0)
        bend_info = {"axis_point": (0.0, 0.0, 0.0), "axis_direction": (1.0, 0.0, 0.0)}

        # Invariant #1: Missing machine frame MUST return UNKNOWN, never VALID
        res = detector.machine_collision(fake_shape, bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)

    def test_machine_frame_loading_when_file_provided(self):
        """When machine_frame.FCStd is provided, PressBrakeMachine loads real B-Rep geometry."""
        user_mf = Path("/Users/badmc/.gemini/antigravity-ide/brain/e6a76cd5-a459-4b91-bdf1-01e5f5e4b005/scratch/user_machine_frame/machine_frame/machine_frame.FCStd")
        if not user_mf.is_file():
            self.skipTest(f"User machine frame scratch file not found: {user_mf}")

        backend = PythonOCCBackend()
        machine = PressBrakeMachine(machine_frame_path=str(user_mf))
        assembly = machine.setup_assembly(backend)

        self.assertIsNotNone(assembly.machine_frame)
        self.assertEqual(assembly.machine_frame.geometry_status, GeometryStatus.REAL)
        world_shape = assembly.get_world_shape(ToolingKind.MACHINE_FRAME)
        self.assertIsNotNone(world_shape)
        self.assertFalse(world_shape.IsNull())


class TestF3bPunchDescentKinematics(unittest.TestCase):
    """Test punch descent offset calculation for animation playback (F.3b)."""

    def test_punch_stroke_calculation(self):
        from freecad_smbs.ui.animation_dialog import _compute_punch_offset_mm

        # Step 0 (initial flat or fully folded at rest): punch is at open height (offset 0.0)
        mock_frame_rest = {"step_index": 0, "is_bending": False}
        offset_rest = _compute_punch_offset_mm(mock_frame_rest, max_stroke_mm=100.0)
        self.assertEqual(offset_rest, 0.0)

        # Active bend step: punch descends to bottom dead center
        mock_frame_bend = {"step_index": 1, "is_bending": True}
        offset_bend = _compute_punch_offset_mm(mock_frame_bend, max_stroke_mm=100.0)
        self.assertGreater(offset_bend, 0.0)
        self.assertLessEqual(offset_bend, 100.0)


if __name__ == "__main__":
    unittest.main()
