"""
test_tooling_library_clamps.py - Unit tests for ToolingLibrary clamp config management (S.24b).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig,
    ClampValidatorError,
    DEFAULT_CLAMP_WIDTH_MM,
    DEFAULT_GAP_BETWEEN_MM,
    DEFAULT_OVERHANG_RATIO,
)
from smbs_core.geometry_backend.io.tooling_library import (
    ToolingLibrary,
    KIND_PUNCH,
    KIND_DIE,
)


class TestToolingLibraryClamps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.library_dir = Path(self.temp_dir) / "lib"
        self.library_dir.mkdir()
        (self.library_dir / KIND_PUNCH).mkdir()
        (self.library_dir / KIND_DIE).mkdir()
        self.lib = ToolingLibrary(self.library_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_clamp_config_missing_returns_default(self):
        """get_clamp_config() without config.json -> default ClampConfig; file is NOT created."""
        cfg = self.lib.get_clamp_config()
        self.assertEqual(cfg.positions_mm, ())
        self.assertEqual(cfg.width_mm, DEFAULT_CLAMP_WIDTH_MM)
        self.assertEqual(cfg.gap_between_mm, DEFAULT_GAP_BETWEEN_MM)
        self.assertEqual(cfg.overhang_ratio, DEFAULT_OVERHANG_RATIO)
        self.assertFalse((self.library_dir / "config.json").exists())

    def test_set_clamp_config_creates_file(self):
        """set_clamp_config(...) creates config.json, valid JSON, get_clamp_config returns it."""
        new_cfg = ClampConfig.from_defaults((75.0, 275.0))
        self.lib.set_clamp_config(new_cfg)

        config_file = self.library_dir / "config.json"
        self.assertTrue(config_file.exists())

        loaded_cfg = self.lib.get_clamp_config()
        self.assertEqual(loaded_cfg, new_cfg)

    def test_set_get_roundtrip(self):
        """set(cfg) -> get() -> roundtrip preserves all fields."""
        cfg = ClampConfig(
            width_mm=160.0,
            gap_between_mm=40.0,
            positions_mm=(80.0, 280.0, 480.0),
            overhang_ratio=0.25,
            joint_tolerance_mm=0.2,
            min_clamps_under_punch=3,
            repositionable=False,
            joints_must_be_on_clamps=True,
            no_gaps_between_punches=True,
        )
        self.lib.set_clamp_config(cfg)
        got = self.lib.get_clamp_config()

        self.assertEqual(cfg, got)
        self.assertEqual(got.width_mm, 160.0)
        self.assertEqual(got.gap_between_mm, 40.0)
        self.assertEqual(got.positions_mm, (80.0, 280.0, 480.0))
        self.assertEqual(got.overhang_ratio, 0.25)
        self.assertEqual(got.joint_tolerance_mm, 0.2)
        self.assertEqual(got.min_clamps_under_punch, 3)
        self.assertFalse(got.repositionable)
        self.assertTrue(got.joints_must_be_on_clamps)
        self.assertTrue(got.no_gaps_between_punches)

    def test_get_malformed_json_raises(self):
        """Malformed JSON in config.json raises ClampValidatorError."""
        config_file = self.library_dir / "config.json"
        config_file.write_text("INVALID { JSON")
        with self.assertRaises(ClampValidatorError):
            self.lib.get_clamp_config()

    def test_get_invalid_schema_raises(self):
        """config.json not conforming to schema raises ClampValidatorError."""
        config_file = self.library_dir / "config.json"
        bad_data = {
            "schema_version": "1.0",
            "clamps": {"width_mm": -1},
        }
        config_file.write_text(json.dumps(bad_data))
        with self.assertRaises(ClampValidatorError):
            self.lib.get_clamp_config()

    def test_ensure_clamp_config_creates_default_when_missing(self):
        """ensure_clamp_config() when missing creates default config.json, repeated ensure returns it."""
        config_file = self.library_dir / "config.json"
        self.assertFalse(config_file.exists())

        res1 = self.lib.ensure_clamp_config()
        self.assertEqual(res1.positions_mm, ())
        self.assertTrue(config_file.exists())

        res2 = self.lib.ensure_clamp_config()
        self.assertEqual(res1, res2)

    def test_ensure_clamp_config_returns_existing(self):
        """ensure_clamp_config() when file exists returns custom config, not overwriting with default."""
        custom_cfg = ClampConfig.from_defaults((100.0, 300.0))
        self.lib.set_clamp_config(custom_cfg)

        res = self.lib.ensure_clamp_config()
        self.assertEqual(res, custom_cfg)
        self.assertEqual(res.positions_mm, (100.0, 300.0))

    def test_ensure_clamp_config_idempotent(self):
        """ensure_clamp_config() called twice returns identical config, content does not change."""
        cfg1 = self.lib.ensure_clamp_config()
        config_file = self.library_dir / "config.json"
        content1 = config_file.read_text()

        cfg2 = self.lib.ensure_clamp_config()
        content2 = config_file.read_text()

        self.assertEqual(cfg1, cfg2)
        self.assertEqual(content1, content2)


if __name__ == "__main__":
    unittest.main()
