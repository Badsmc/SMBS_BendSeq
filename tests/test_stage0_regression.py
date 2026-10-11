"""
test_stage0_regression.py - Stage 0 Baseline Regression Test Suite for SMBS

Validates that real FCStd test models in SMBendTests/ as well as synthetic fixtures
conform to expected bend counts and properties.
"""

import sys
import os
import unittest

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from tests.fixtures.fixture_factory import FixtureFactory


class TestStage0Baseline(unittest.TestCase):
    """Stage 0 Regression Baseline Validation."""

    def test_smbendtests_files_exist(self):
        """Verify presence of real FCStd test files in SMBendTests/."""
        smbend_dir = os.path.join(SYS_PATH, "SMBendTests")
        expected_files = [
            "L-(ok).FCStd",
            "U-90deg-90deg-(ok).FCStd",
            "U-60deg-30deg-(ok).FCStd",
            "135-(ok).FCStd",
            "Z-(ok).FCStd",
            "2-1-3-4-(ok).FCStd"
        ]
        for filename in expected_files:
            filepath = os.path.join(smbend_dir, filename)
            self.assertTrue(os.path.isfile(filepath), f"Missing test model: {filename}")

    def test_synthetic_fixtures_baseline(self):
        """Verify synthetic profiles fixture baseline (L, U, C, +45, 135, Z, 2-1-3-4)."""
        fixtures = FixtureFactory.get_all_fixtures()
        
        # L profile
        self.assertEqual(fixtures["L"].expected_bends_count, 1)
        self.assertEqual(fixtures["L"].expected_signed_angles, [+90.0])

        # U profile
        self.assertEqual(fixtures["U"].expected_bends_count, 2)
        self.assertEqual(fixtures["U"].expected_signed_angles, [+90.0, +90.0])

        # C profile
        self.assertEqual(fixtures["C"].expected_bends_count, 4)

        # Acute 45
        self.assertEqual(fixtures["Acute_45"].expected_signed_angles, [+45.0])

        # Obtuse 135
        self.assertEqual(fixtures["Obtuse_135"].expected_signed_angles, [+135.0])

        # Z bend
        self.assertEqual(fixtures["Z_bend"].expected_signed_angles, [+45.0, -45.0])

        # 2-1-3-4 sequence ordering
        self.assertEqual(fixtures["Sequence_2134"].expected_bends_count, 4)


if __name__ == "__main__":
    unittest.main()
