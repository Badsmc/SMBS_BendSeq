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
    """Verify KL-16 honest UNKNOWN when machine_frame is missing, real shape and collision when present."""

    def test_machine_frame_missing_honest_unknown(self):
        """When machine_frame is not supplied, assembly has no machine frame and collision is UNKNOWN."""
        repo_root = Path(__file__).resolve().parents[1]
        puancon = repo_root / "toollibrary" / "punch" / "puancon.FCStd"
        matrica = repo_root / "toollibrary" / "die" / "matrica.FCStd"

        backend = PythonOCCBackend()
        # Machine with real punch/die but NO machine frame
        machine = PressBrakeMachine(
            punch_path=str(puancon) if puancon.exists() else None,
            die_path=str(matrica) if matrica.exists() else None,
            punch_target_object_name="Body",
            die_target_object_name="Body",
            machine_frame_path=None,
        )
        assembly = machine.setup_assembly(backend)

        # Machine frame must be None (no fabricated geometry, Invariant #1)
        self.assertIsNone(assembly.machine_frame)
        self.assertIsNone(assembly.get_world_shape(ToolingKind.MACHINE_FRAME))

        from smbs_core.Physics.collision_detector import CollisionDetector
        detector = CollisionDetector(assembly, backend)
        small_shape = backend.make_box(20.0, 20.0, 1.0)
        bend_info = {
            "origin": (0.0, 0.0, 0.0),
            "axis": (1.0, 0.0, 0.0),
            "normal": (0.0, 0.0, 1.0),
            "flange_length": 10.0,
            "angle": 90.0,
        }

        # Invariant #1: Missing machine frame MUST return UNKNOWN, never VALID
        res = detector.machine_collision(small_shape, bend_info)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        self.assertFalse(res.is_valid)
        self.assertIn("No machine_frame", res.message)

    def test_machine_frame_loading_when_file_provided(self):
        """When machine_frame.FCStd is provided, PressBrakeMachine loads real B-Rep geometry and runs collision check."""
        repo_root = Path(__file__).resolve().parents[1]
        mf_path = repo_root / "toollibrary" / "machine_frame" / "machine_frame.FCStd"
        self.assertTrue(mf_path.is_file(), f"Machine frame file not found: {mf_path}")

        backend = PythonOCCBackend()
        machine = PressBrakeMachine.with_default_tooling()
        self.assertIsNotNone(machine.machine_frame_path)
        assembly = machine.setup_assembly(backend)

        # Real geometry loaded from FCStd
        self.assertIsNotNone(assembly.machine_frame)
        self.assertEqual(assembly.machine_frame.geometry_status, GeometryStatus.REAL)
        world_shape = assembly.get_world_shape(ToolingKind.MACHINE_FRAME)
        self.assertIsNotNone(world_shape)
        self.assertFalse(world_shape.IsNull())

        from smbs_core.Physics.collision_detector import CollisionDetector
        detector = CollisionDetector(assembly, backend)
        bend_info = {
            "origin": (0.0, 0.0, 0.0),
            "axis": (1.0, 0.0, 0.0),
            "normal": (0.0, 0.0, 1.0),
            "flange_length": 10.0,
            "angle": 90.0,
        }

        # 1. Positive clear test: small shape within press bed working area -> VALID Clear
        small_shape = backend.make_box(20.0, 20.0, 1.0)
        res_clear = detector.machine_collision(small_shape, bend_info)
        self.assertEqual(res_clear.status, CollisionStatus.VALID)
        self.assertTrue(res_clear.is_valid)

        # 2. Positive collision test: huge shape penetrating the frame column -> INVALID
        huge_shape = backend.make_box(5000.0, 5000.0, 1000.0)
        res_coll = detector.machine_collision(huge_shape, bend_info)
        self.assertEqual(res_coll.status, CollisionStatus.INVALID)
        self.assertFalse(res_coll.is_valid)
        self.assertIn("Intersection detected", res_coll.message)

    def test_zero_geometry_fabrication_on_error(self):
        """When machine frame loading encounters an error or corrupted file, do NOT fabricate geometry."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False) as f:
            f.write(b"NOT_A_VALID_FREE_CAD_FILE_CORRUPTED_BYTES")
            corrupt_path = f.name

        try:
            backend = PythonOCCBackend()
            machine = PressBrakeMachine(machine_frame_path=corrupt_path)
            assembly = machine.setup_assembly(backend)

            # Invariant #1: frame must be None, no fabricated box
            self.assertIsNone(assembly.machine_frame)
            self.assertIsNone(assembly.get_world_shape(ToolingKind.MACHINE_FRAME))

            from smbs_core.Physics.collision_detector import CollisionDetector
            detector = CollisionDetector(assembly, backend)
            small_shape = backend.make_box(20.0, 20.0, 1.0)
            bend_info = {"axis": (1.0, 0.0, 0.0), "origin": (0.0, 0.0, 0.0)}
            res = detector.machine_collision(small_shape, bend_info)
            self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        finally:
            if os.path.exists(corrupt_path):
                os.remove(corrupt_path)

    def test_validator_machine_stage(self):
        """PhysicalValidator MACHINE stage produces VALID when frame is present and clear, UNKNOWN when missing."""
        from smbs_core.Physics.validator import PhysicalValidator
        from smbs_core.Core.bend_graph import BendGraph, BendRecord
        from smbs_core.Core.panel_graph import PanelGraph, Panel

        backend = PythonOCCBackend()
        repo_root = Path(__file__).resolve().parents[1]
        puancon = repo_root / "toollibrary" / "punch" / "puancon.FCStd"
        matrica = repo_root / "toollibrary" / "die" / "matrica.FCStd"

        part_shape = backend.make_box(20.0, 20.0, 1.0)
        bend_info = {
            "origin": (0.0, 0.0, 0.0),
            "axis": (1.0, 0.0, 0.0),
            "normal": (0.0, 0.0, 1.0),
            "flange_length": 10.0,
            "angle": 90.0,
        }

        # 1. Machine with frame -> MACHINE stage evaluates without UNKNOWN (VALID)
        machine_with_frame = PressBrakeMachine.with_default_tooling()
        val_with_frame = PhysicalValidator(machine_with_frame, backend)
        mach_res = val_with_frame.detector.machine_collision(part_shape, bend_info)
        self.assertEqual(mach_res.status, CollisionStatus.VALID)
        self.assertTrue(mach_res.is_valid)

        # 2. Machine without frame -> MACHINE stage evaluates to UNKNOWN (honest)
        machine_no_frame = PressBrakeMachine(
            punch_path=str(puancon) if puancon.exists() else None,
            die_path=str(matrica) if matrica.exists() else None,
            punch_target_object_name="Body",
            die_target_object_name="Body",
            machine_frame_path=None,
        )
        val_no_frame = PhysicalValidator(machine_no_frame, backend)
        mach_res_none = val_no_frame.detector.machine_collision(part_shape, bend_info)
        self.assertEqual(mach_res_none.status, CollisionStatus.UNKNOWN)
        self.assertFalse(mach_res_none.is_valid)


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
