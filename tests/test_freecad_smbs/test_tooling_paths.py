"""
Unit tests for canonical tooling paths (toollibrary/) and status badges (Commit C2).
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
    is_tool_available,
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
    def test_tooling_paths_are_toollibrary(self):
        """C2-4 exact assertions for canonical toollibrary structure."""
        # Punch path checks
        self.assertEqual(PUNCH_PATH.name, "puancon.FCStd")
        self.assertEqual(PUNCH_PATH.parent.name, "punch")
        self.assertEqual(PUNCH_PATH.parent.parent.name, "toollibrary")
        self.assertTrue(PUNCH_PATH.exists(), f"Expected punch file to exist: {PUNCH_PATH}")
        self.assertTrue(is_tool_available("punch"))

        # Die path checks
        self.assertEqual(DIE_PATH.name, "matrica.FCStd")
        self.assertEqual(DIE_PATH.parent.name, "die")
        self.assertEqual(DIE_PATH.parent.parent.name, "toollibrary")
        self.assertTrue(DIE_PATH.exists(), f"Expected die file to exist: {DIE_PATH}")
        self.assertTrue(is_tool_available("die"))

        # Press path checks
        self.assertEqual(PRESS_PATH.name, "cncbender.FCStd")
        self.assertEqual(PRESS_PATH.parent.name, "presses")
        self.assertEqual(PRESS_PATH.parent.parent.name, "toollibrary")
        self.assertTrue(PRESS_PATH.exists(), f"Expected press file to exist: {PRESS_PATH}")
        self.assertTrue(is_tool_available("press"))

        # Missing tools checks (β1 — expected convention, but files are missing)
        self.assertEqual(BACKGAUGE_PATH.name, "backgauge.FCStd")
        self.assertFalse(BACKGAUGE_PATH.exists(), "Backgauge file must be MISSING in β1")
        self.assertFalse(is_tool_available("backgauge"))

        self.assertEqual(CLAMPS_PATH.name, "clamps.FCStd")
        self.assertFalse(CLAMPS_PATH.exists(), "Clamps file must be MISSING in β1")
        self.assertFalse(is_tool_available("clamps"))

        self.assertEqual(MACHINE_FRAME_PATH.name, "machine_frame.FCStd")
        self.assertFalse(MACHINE_FRAME_PATH.exists(), "Machine frame file must be MISSING in β1")
        self.assertFalse(is_tool_available("machine_frame"))

        # Verify missing tools list
        self.assertIn("backgauge", MISSING_TOOLS)
        self.assertIn("clamps", MISSING_TOOLS)
        self.assertIn("machine_frame", MISSING_TOOLS)

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
