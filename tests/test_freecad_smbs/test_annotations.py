"""
Unit tests for compute_annotation_positions (Commit C4).
Headless, pure Python tests verifying Z-override and positioning contracts.
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Tuple

from freecad_smbs.ui.annotations import (
    ANNOTATION_FONT_SIZE,
    ANNOTATION_Z_OFFSET_MM,
    compute_annotation_positions,
)


@dataclass(frozen=True)
class DummyAnnotation:
    step: int
    bend_id: str
    midpoint: Tuple[float, float, float]
    ui_direction: str
    verification: str


class TestAnnotationPositions(unittest.TestCase):
    def test_constants(self):
        """Verify macro formatting parity constants."""
        self.assertEqual(ANNOTATION_Z_OFFSET_MM, 3.0)
        self.assertEqual(ANNOTATION_FONT_SIZE, 15)

    def test_z_override(self):
        """Verify that flat_zmax + 3.0 overrides annotation midpoint[2]."""
        ann = DummyAnnotation(
            step=1,
            bend_id="B01",
            midpoint=(10.0, 20.0, 5.0),
            ui_direction="up",
            verification="VERIFIED",
        )
        positions = compute_annotation_positions([ann], flat_zmax=0.0)
        self.assertEqual(len(positions), 1)
        step, x, y, z, verif = positions[0]
        self.assertEqual(step, 1)
        self.assertEqual(x, 10.0)
        self.assertEqual(y, 20.0)
        # midpoint[2] is 5.0, but output z MUST be flat_zmax (0.0) + 3.0 = 3.0
        self.assertEqual(z, 3.0)
        self.assertEqual(verif, "VERIFIED")

    def test_multiple_positions(self):
        """Verify multiple annotations with non-zero flat_zmax."""
        anns = [
            DummyAnnotation(
                step=1,
                bend_id="B01",
                midpoint=(0.0, 10.0, 1.0),
                ui_direction="up",
                verification="UNVERIFIED",
            ),
            DummyAnnotation(
                step=2,
                bend_id="B02",
                midpoint=(50.0, 10.0, 2.0),
                ui_direction="down",
                verification="UNVERIFIED",
            ),
        ]
        positions = compute_annotation_positions(anns, flat_zmax=2.5)
        self.assertEqual(len(positions), 2)
        # flat_zmax (2.5) + ANNOTATION_Z_OFFSET_MM (3.0) = 5.5
        self.assertEqual(positions[0], (1, 0.0, 10.0, 5.5, "UNVERIFIED"))
        self.assertEqual(positions[1], (2, 50.0, 10.0, 5.5, "UNVERIFIED"))
