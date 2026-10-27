"""
Unit tests for canonical tooling paths (toollibrary/) and status badges (S.71 C2b).
"""
from __future__ import annotations

import unittest
from pathlib import Path

from freecad_smbs.resources.tooling_paths import (
    ALL_TOOLING_PATHS,
    BACKGAUGE_PATH,
    CLAMPS_PATH,
    DIE_PATH,
    MACHINE_FRAME_PATH,
    MISSING_TOOLS,
    PRESS_PATH,
    PUNCH_PATH,
    TOOL_LIBRARY_ROOT,
    is_tool_available,
    resolve_backgauge_fcstd,
    resolve_clamp_fcstd,
    resolve_machine_frame_fcstd,
)
from freecad_smbs.ui.badges import (
    BADGE_COLORS_HEX,
    BADGE_COLORS_RGBA,
    BADGE_MACHINE_SIMPLIFIED,
    BADGE_PREVIEW_ONLY,
    BADGE_TOOLING_MISSING,
    BADGE_UNVERIFIED,
    BADGE_VERIFICATION_FAILED,
    BADGE_VERIFIED,
    get_badge_hex,
    get_badge_rgba,
)


class TestToolingPathsAndBadges(unittest.TestCase):
    def test_tooling_paths_canonical_names(self):
        """Canonical path names + structure (S.69 β1 preserved). KL-16 machine_frame still missing."""
        # Existing tools present
        self.assertEqual(PUNCH_PATH.name, "puancon.FCStd")
        self.assertEqual(PUNCH_PATH.parent.name, "punch")
        self.assertEqual(PUNCH_PATH.parent.parent.name, "toollibrary")
        self.assertTrue(PUNCH_PATH.exists())

        self.assertEqual(DIE_PATH.name, "matrica.FCStd")
        self.assertEqual(DIE_PATH.parent.name, "die")
        self.assertTrue(DIE_PATH.exists())

        self.assertEqual(PRESS_PATH.name, "cncbender.FCStd")
        self.assertEqual(PRESS_PATH.parent.name, "presses")
        self.assertTrue(PRESS_PATH.exists())

        # Canonical path names (β1 conventions preserved)
        self.assertEqual(BACKGAUGE_PATH.name, "backgauge.FCStd")
        self.assertEqual(CLAMPS_PATH.name, "clamps.FCStd")
        self.assertEqual(MACHINE_FRAME_PATH.name, "machine_frame.FCStd")

        # KL-16: machine_frame is present in toollibrary/machine_frame/
        self.assertTrue(MACHINE_FRAME_PATH.exists())
        self.assertNotIn("machine_frame", MISSING_TOOLS)

    def test_tooling_paths_resolvers_find_real_files(self):
        """Resolvers locate real backgauge/clamp/machine_frame files on disk."""
        bg = resolve_backgauge_fcstd(TOOL_LIBRARY_ROOT)
        cl = resolve_clamp_fcstd(TOOL_LIBRARY_ROOT)
        mf = resolve_machine_frame_fcstd(TOOL_LIBRARY_ROOT)
        self.assertIsNotNone(bg, f"Backgauge file not found in {TOOL_LIBRARY_ROOT}")
        self.assertIsNotNone(cl, f"Clamp file not found in {TOOL_LIBRARY_ROOT}")
        self.assertIsNotNone(mf, f"Machine frame file not found in {TOOL_LIBRARY_ROOT}")
        self.assertEqual(bg.suffix.lower(), ".fcstd")
        self.assertEqual(cl.suffix.lower(), ".fcstd")
        self.assertEqual(mf.suffix.lower(), ".fcstd")

    def test_badges_color_mapping(self):
        """Verify badges and color mappings are pure Python and complete."""
        badges = [
            BADGE_VERIFIED,
            BADGE_UNVERIFIED,
            BADGE_VERIFICATION_FAILED,
            BADGE_PREVIEW_ONLY,
            BADGE_MACHINE_SIMPLIFIED,
            BADGE_TOOLING_MISSING,
        ]
        for badge in badges:
            rgba = get_badge_rgba(badge)
            self.assertEqual(len(rgba), 4)
            self.assertTrue(all(0.0 <= c <= 1.0 for c in rgba))

            hex_code = get_badge_hex(badge)
            self.assertTrue(hex_code.startswith("#"))
            self.assertEqual(len(hex_code), 7)

    def test_resolve_backgauge_fcstd_case_insensitive(self):
        """Resolver finds Backgauge/Backgauge.FCStd (capital case) via fallback."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Backgauge").mkdir()
            target = root / "Backgauge" / "Backgauge.FCStd"
            target.write_bytes(b"dummy")
            result = resolve_backgauge_fcstd(root)
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "Backgauge.FCStd")

    def test_resolve_clamp_fcstd_case_insensitive(self):
        """Resolver finds Clamp/Clamp.FCStd (singular, capital) via fallback."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Clamp").mkdir()
            target = root / "Clamp" / "Clamp.FCStd"
            target.write_bytes(b"dummy")
            result = resolve_clamp_fcstd(root)
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "Clamp.FCStd")

    def test_resolve_machine_frame_fcstd_fallback(self):
        """Resolver finds machine_frame.FCStd in machine_frame/ or fallback dirs."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "machine_frame").mkdir()
            target = root / "machine_frame" / "machine_frame.FCStd"
            target.write_bytes(b"dummy")
            result = resolve_machine_frame_fcstd(root)
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "machine_frame.FCStd")


if __name__ == "__main__":
    unittest.main()
