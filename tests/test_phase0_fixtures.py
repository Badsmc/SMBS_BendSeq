"""
test_phase0_fixtures.py - Phase 0 Regression Test Suite for SMBS DEVELOPMENT SPEC v1.0

Validates that all Phase 0 regression fixtures:
- L Profile (single 90-deg bend)
- U Channel (two parallel 90-deg bends)
- C Profile (four 90-deg box bends)
- Acute +45° Bend
- Obtuse +135° Bend
- Z-Bend (+45° / -45° opposing signs)
- Sequence Order 2-1-3-4 (multi-bend physical clearance ordering)

are properly instantiated with expected signed angles, panel counts, and bend specifications.
"""

import sys
import os
import unittest

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from tests.fixtures.fixture_factory import FixtureFactory, FixtureSpec


class TestPhase0Fixtures(unittest.TestCase):
    """Phase 0 Regression Fixture Validation."""

    def test_fixture_l_profile(self):
        spec = FixtureFactory.create_l_profile()
        self.assertEqual(spec.name, "L_profile")
        self.assertEqual(spec.expected_bends_count, 1)
        self.assertEqual(spec.expected_signed_angles, [+90.0])
        self.assertEqual(spec.expected_panels_count, 2)
        self.assertIn(["BEND_01"], spec.expected_valid_sequences)

    def test_fixture_u_channel(self):
        spec = FixtureFactory.create_u_channel()
        self.assertEqual(spec.name, "U_channel")
        self.assertEqual(spec.expected_bends_count, 2)
        self.assertEqual(spec.expected_signed_angles, [+90.0, +90.0])
        self.assertEqual(spec.expected_panels_count, 3)
        self.assertEqual(len(spec.expected_valid_sequences), 2)

    def test_fixture_c_profile(self):
        spec = FixtureFactory.create_c_profile()
        self.assertEqual(spec.name, "C_profile")
        self.assertEqual(spec.expected_bends_count, 4)
        self.assertEqual(spec.expected_signed_angles, [+90.0, +90.0, +90.0, +90.0])
        self.assertEqual(spec.expected_panels_count, 5)

    def test_fixture_acute_45(self):
        spec = FixtureFactory.create_acute_45()
        self.assertEqual(spec.name, "Acute_45")
        self.assertEqual(spec.expected_bends_count, 1)
        self.assertEqual(spec.expected_signed_angles, [+45.0])
        self.assertEqual(spec.expected_panels_count, 2)

    def test_fixture_obtuse_135(self):
        spec = FixtureFactory.create_obtuse_135()
        self.assertEqual(spec.name, "Obtuse_135")
        self.assertEqual(spec.expected_bends_count, 1)
        self.assertEqual(spec.expected_signed_angles, [+135.0])

    def test_fixture_z_bend(self):
        spec = FixtureFactory.create_opposing_z_bend()
        self.assertEqual(spec.name, "Z_bend_45_neg45")
        self.assertEqual(spec.expected_bends_count, 2)
        self.assertEqual(spec.expected_signed_angles, [+45.0, -45.0])

    def test_fixture_sequence_2134(self):
        spec = FixtureFactory.create_sequence_2134()
        self.assertEqual(spec.name, "Sequence_2134")
        self.assertEqual(spec.expected_bends_count, 4)
        self.assertEqual(spec.expected_valid_sequences, [["BEND_02", "BEND_01", "BEND_03", "BEND_04"]])

    def test_all_fixtures_factory(self):
        fixtures = FixtureFactory.get_all_fixtures()
        self.assertEqual(len(fixtures), 7)
        self.assertIn("L", fixtures)
        self.assertIn("U", fixtures)
        self.assertIn("C", fixtures)
        self.assertIn("Acute_45", fixtures)
        self.assertIn("Obtuse_135", fixtures)
        self.assertIn("Z_bend", fixtures)
        self.assertIn("Sequence_2134", fixtures)


if __name__ == "__main__":
    unittest.main()
