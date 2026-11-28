import unittest
from dataclasses import dataclass
from types import SimpleNamespace as NS
from freecad.bendseq.core.kinematics2d.auto_tool_selection import ToolCandidate
from freecad.bendseq.core.kinematics2d.auto_tool_orientation import (
    variant_key, orientation_variants, oriented_punch, restore_matching_punch)
from freecad.bendseq.core.kinematics2d.auto_tool_retry import attempt_order


@dataclass
class Profile:
    tool_id: str = "P"
    punch_reversed: bool = False
    geometry: str = "original"


class AutoToolOrientationTests(unittest.TestCase):
    def test_variants_separate_and_no_stock_duplication(self):
        c = ToolCandidate("P", "D", 12, {}, (), ())
        variants = orientation_variants([c], [NS(tool_id="P", metadata={})], enabled=True)
        self.assertEqual([variant_key(v) for v in variants], [("P", "D", False), ("P", "D", True)])
        self.assertIs(variants[0].plan, variants[1].plan)
        self.assertEqual(attempt_order((variants[0], 1), [(variants[1], 2)], retry=True),
                         ((variants[0], 1), (variants[1], 2)))

    def test_disabled_or_prohibited_uses_default(self):
        c = ToolCandidate("P", "D", 12, {}, (), ())
        descriptor = NS(tool_id="P", metadata={"allow_reverse_punch": False})
        self.assertIsNone(orientation_variants([c], [descriptor], enabled=True)[0].punch_reversed)
        self.assertIsNone(orientation_variants([c], [descriptor], enabled=False)[0].punch_reversed)

    def test_profile_copy_never_changes_library(self):
        profile = Profile()
        reversed_profile = oriented_punch(profile, True)
        self.assertFalse(profile.punch_reversed)
        self.assertTrue(reversed_profile.punch_reversed)
        self.assertIsNot(profile, reversed_profile)
        self.assertFalse(oriented_punch(profile, None).punch_reversed)

    def test_restore_requires_exact_fingerprint(self):
        profile = Profile()
        matches = lambda p, ref: (p.tool_id, p.punch_reversed, p.geometry) == ref
        restored = restore_matching_punch(profile, ("P", True, "original"), matches)
        self.assertTrue(restored.punch_reversed)
        self.assertFalse(profile.punch_reversed)
        with self.assertRaisesRegex(ValueError, "profile changed"):
            restore_matching_punch(profile, ("P", True, "changed"), matches)
