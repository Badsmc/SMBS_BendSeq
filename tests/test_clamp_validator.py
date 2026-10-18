import json
import tempfile
import unittest
from pathlib import Path

from smbs_core.geometry_backend.io.clamp_validator import (
    ClampConfig,
    PunchSegment,
    ClampValidatorError,
    validate_punch_chain,
    load_clamp_config,
    save_clamp_config,
    DEFAULT_CLAMP_WIDTH_MM,
    DEFAULT_GAP_BETWEEN_MM,
)


class TestClampValidator(unittest.TestCase):
    def setUp(self):
        # 3 clamps: centers at 75, 275, 475. Width=150, gap=50.
        # Zones: [0..150], [200..350], [400..550]
        self.positions = (75.0, 275.0, 475.0)
        self.config = ClampConfig.from_defaults(self.positions)

    def test_empty_chain_returns_error(self):
        """Empty punch chain fails validation."""
        res = validate_punch_chain([], self.config)
        self.assertFalse(res.valid)
        self.assertIn("cannot be empty", res.errors[0])

    def test_single_punch_two_clamps_coverage_ok(self):
        """Single punch spanning across at least 2 clamps -> OK."""
        # Punch from 50 to 300 (intersects [0..150] and [200..350])
        chain = [PunchSegment(50.0, 300.0)]
        res = validate_punch_chain(chain, self.config)
        self.assertTrue(res.valid)
        self.assertEqual(len(res.errors), 0)

    def test_single_punch_single_clamp_coverage_fails(self):
        """Single punch covering only 1 clamp fails (requires at least 2)."""
        # Punch from 20 to 120 (only intersects [0..150])
        chain = [PunchSegment(20.0, 120.0)]
        res = validate_punch_chain(chain, self.config)
        self.assertFalse(res.valid)
        self.assertIn("Insufficient clamp blocks", res.errors[0])

    def test_chain_with_gap_fails(self):
        """Gaps between punch segments violate Rule 2."""
        chain = [
            PunchSegment(50.0, 150.0),
            PunchSegment(160.0, 300.0),  # Gap of 10mm
        ]
        res = validate_punch_chain(chain, self.config)
        self.assertFalse(res.valid)
        self.assertIn("Gap or overlap", res.errors[0])

    def test_joint_in_clamp_zone_ok(self):
        """Joints located inside clamp zones pass validation."""
        # Zones: [0..150], [200..350], [400..550]
        # Joint at 250 (inside [200..350])
        chain = [
            PunchSegment(50.0, 250.0),
            PunchSegment(250.0, 450.0),
        ]
        res = validate_punch_chain(chain, self.config)
        self.assertTrue(res.valid)
        self.assertEqual(len(res.errors), 0)

    def test_joint_in_gap_between_clamps_fails(self):
        """Joints falling in gap between clamp zones fail Rule 4."""
        # Gap between blocks is [150..200]. Place joint at 175.
        chain = [
            PunchSegment(50.0, 175.0),
            PunchSegment(175.0, 300.0),
        ]
        res = validate_punch_chain(chain, self.config)
        self.assertFalse(res.valid)
        self.assertIn("falls in gap between clamps", res.errors[0])

    def test_left_overhang_ok(self):
        """Left overhang <= 30% of punch length passes."""
        # First block zone [0..150]. Punch starts at -15. Overhang = 15mm.
        # Punch length: 265mm. 30% of 265 = 79.5mm. 15 <= 79.5 -> OK.
        chain = [
            PunchSegment(-15.0, 250.0),
            PunchSegment(250.0, 450.0),
        ]
        res = validate_punch_chain(chain, self.config)
        self.assertTrue(res.valid)

    def test_left_overhang_fails(self):
        """Left overhang > 30% of punch length fails Rule 5."""
        # First block zone [0..150]. Punch starts at -50. Overhang = 50mm.
        # Punch length: 150 - (-50) = 200 - 100 = 100mm. 30% of 100 = 30mm. 50 > 30 -> FAIL.
        chain = [
            PunchSegment(-50.0, 50.0),
            PunchSegment(50.0, 250.0),
        ]
        res = validate_punch_chain(chain, self.config)
        self.assertFalse(res.valid)
        self.assertIn("Left punch overhang", res.errors[0])

    def test_right_overhang_ok_and_fail(self):
        """Right overhang within / exceeding 30% behaves correctly."""
        # Last block zone [400..550].
        # 1. Right overhang OK: end at 570, overhang=20mm, punch length = 570 - 250 = 320mm (30% = 96mm) -> OK
        chain_ok = [
            PunchSegment(100.0, 250.0),
            PunchSegment(250.0, 570.0),
        ]
        res_ok = validate_punch_chain(chain_ok, self.config)
        self.assertTrue(res_ok.valid)

        # 2. Right overhang FAIL: end at 620, overhang=70mm. Punch length = 620 - 500 = 120mm (30% = 36mm). 70 > 36 -> FAIL
        chain_fail = [
            PunchSegment(100.0, 250.0),
            PunchSegment(250.0, 500.0),
            PunchSegment(500.0, 620.0),
        ]
        res_fail = validate_punch_chain(chain_fail, self.config)
        self.assertFalse(res_fail.valid)
        self.assertIn("Right punch overhang", res_fail.errors[0])

    def test_roundtrip_from_dict_to_dict(self):
        """ClampConfig round-trip from_dict <-> to_dict preserves all values."""
        cfg = ClampConfig.from_defaults((100.0, 300.0, 500.0))
        d = cfg.to_dict()
        cfg_recovered = ClampConfig.from_dict(d)
        self.assertEqual(cfg, cfg_recovered)
        self.assertEqual(d, cfg_recovered.to_dict())

    def test_from_dict_missing_field_raises(self):
        """ClampConfig.from_dict raises ClampValidatorError on incomplete dict."""
        bad_dict = {
            "schema_version": "1.0",
            "clamps": {"width_mm": 150.0},  # missing required fields
        }
        with self.assertRaises(ClampValidatorError):
            ClampConfig.from_dict(bad_dict)

    def test_load_and_save_clamp_config(self):
        """load_clamp_config and save_clamp_config write and read atomic JSON."""
        with tempfile.TemporaryDirectory() as td:
            lib_root = Path(td)
            # Default when missing
            default_cfg = load_clamp_config(lib_root)
            self.assertEqual(default_cfg.positions_mm, ())

            # Save explicit config
            custom_cfg = ClampConfig.from_defaults((50.0, 250.0))
            save_clamp_config(lib_root, custom_cfg)

            loaded_cfg = load_clamp_config(lib_root)
            self.assertEqual(loaded_cfg, custom_cfg)
            self.assertEqual(loaded_cfg.positions_mm, (50.0, 250.0))


    def test_gaps_allowed_when_flag_disabled(self):
        """If no_gaps_between_punches=False, gaps between punches are allowed."""
        config = ClampConfig(
            width_mm=150.0,
            gap_between_mm=50.0,
            positions_mm=(75.0, 275.0, 475.0),
            overhang_ratio=0.3,
            joint_tolerance_mm=0.1,
            min_clamps_under_punch=2,
            no_gaps_between_punches=False,  # gaps explicitly allowed
        )
        # Joint at 150 (on clamp 1 boundary), gap 150->160 to next punch
        chain = [
            PunchSegment(50.0, 150.0),
            PunchSegment(160.0, 300.0),
        ]
        res = validate_punch_chain(chain, config)
        # 150 in [0..150] -> joint OK
        # chain_end=300, last_block [200..350] end=350, 300 <= 350 -> no right overhang
        # chain_start=50, first_block [0..150] start=0, 50 >= 0 -> no left overhang
        self.assertTrue(res.valid)
        self.assertEqual(len(res.errors), 0)


if __name__ == "__main__":
    unittest.main()
