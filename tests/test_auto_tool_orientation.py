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


    def test_real_asymmetric_profile_and_native_loader_share_orientation(self):
        from freecad.bendseq.core.kinematics2d.tooling_25d import (
            ToolProfile, ToolKind, ProfileGeometry2D, Contour2D, LineSegment2D)
        from freecad.bendseq.core.kinematics2d.tooling_assembly import native_section_options
        from freecad.bendseq.integration.fcstd_session_store import tool_reference, matches_tool_reference
        points = ((0, 0), (4, 10), (-1, 10), (0, 0))
        geometry = ProfileGeometry2D(Contour2D(tuple(
            LineSegment2D(a, b) for a, b in zip(points, points[1:]))))
        normal = ToolProfile("P", "Asymmetric", ToolKind.PUNCH, "FCSTD",
                             "P.FCStd", "mm", geometry)
        before, _ = normal.local_polygons()
        reversed_profile = oriented_punch(normal, True)
        after, _ = reversed_profile.local_polygons()
        self.assertEqual(set(after), {(-s, z) for s, z in before})
        self.assertNotEqual(set(before), set(after))
        self.assertEqual(normal.local_polygons()[0], before)
        self.assertFalse(native_section_options(NS(punch=normal), "punch")["reverse_punch"])
        self.assertTrue(native_section_options(NS(punch=reversed_profile), "punch")["reverse_punch"])
        restored = restore_matching_punch(normal, tool_reference(reversed_profile), matches_tool_reference)
        self.assertTrue(restored.punch_reversed)
        self.assertEqual(tool_reference(restored), tool_reference(reversed_profile))
