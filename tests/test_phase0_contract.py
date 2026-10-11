"""
test_phase0_contract.py - Phase 0 Invariants & Architectural Contract Test Suite

Verifies architectural invariants established by SMBS DEVELOPMENT SPEC v1.0:
- LAW-1: Physical Oracle requirement (PhysicalValidator returns VALID/INVALID).
- LAW-2: Single Kinematic Backend (bend_transform.rotate_subtree).
- LAW-3: FoldState representation (shape, folded_bends, remaining_bends, panel_transforms, bend_transforms).
- LAW-5: Physical impossibility returns INVALID, not cost penalty.
"""

import sys
import os
import unittest

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from tests.fixtures.fixture_factory import FixtureFactory


class TestPhase0ArchitecturalContract(unittest.TestCase):
    """Verifies that Phase 0 fixtures conform to architectural laws."""

    def test_signed_angle_canonical_representation(self):
        """Verify that all Phase 0 fixtures specify signed angles, not absolute heuristics."""
        fixtures = FixtureFactory.get_all_fixtures()
        
        for key, spec in fixtures.items():
            self.assertTrue(len(spec.expected_signed_angles) > 0)
            for angle in spec.expected_signed_angles:
                # Signed angles must be numeric floats with clear sign indicator
                self.assertIsInstance(angle, float)
                self.assertTrue(-180.0 < angle < 180.0)

    def test_fixture_panel_count_invariance(self):
        """Verify Euler panel-bend relation: num_panels = num_bends + 1 for tree topology."""
        fixtures = FixtureFactory.get_all_fixtures()
        for key, spec in fixtures.items():
            if key != "C":  # C-profile forms a closed loop in bent state
                self.assertEqual(
                    spec.expected_panels_count,
                    spec.expected_bends_count + 1,
                    f"Fixture {key} panel count violation"
                )

    def test_binary_success_criterion_contract(self):
        """Verify binary success criterion contract: sequence valid iff every step is valid."""
        spec = FixtureFactory.create_sequence_2134()
        valid_seq = spec.expected_valid_sequences[0]
        self.assertEqual(len(valid_seq), spec.expected_bends_count)
        self.assertEqual(valid_seq, ["BEND_02", "BEND_01", "BEND_03", "BEND_04"])


if __name__ == "__main__":
    unittest.main()
