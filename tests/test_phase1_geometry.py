"""
test_phase1_geometry.py - Phase 1 Geometry Model Test Suite

Verifies:
- BendRecord validation and IncompleteBendError on bad parameters.
- FeatureExtractor signed angle calculation math:
    α = arccos(clamp(n1 · n2, -1, 1))
    s = sign(a · (n1 × n2))
    θ = s * α
- PanelGraph topological panel tree & moving_subtree / fixed_set resolution.
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

from smbs_core.Core.bend_graph import BendRecord, BendGraph, IncompleteBendError
from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.Geometry.feature_extractor import FeatureExtractor


class TestPhase1GeometryModel(unittest.TestCase):
    """Phase 1 Geometry Model & Canonical Representation Tests."""

    def test_bend_record_validation(self):
        """Test BendRecord canonical fields and validation."""
        rec = BendRecord(
            bend_id="BEND_01",
            feature_id="FEAT_01",
            parent_panel_id="PANEL_A",
            child_panel_id="PANEL_B",
            axis_origin=(0.0, 0.0, 0.0),
            axis_direction=(1.0, 0.0, 0.0),
            signed_angle=+90.0,
            radius=2.0,
            thickness=2.0,
            length=100.0
        )
        self.assertEqual(rec.id, "BEND_01")
        self.assertEqual(rec.signed_angle, 90.0)
        self.assertEqual(rec.unsigned_angle, 90.0)
        self.assertEqual(rec.axis_direction, (1.0, 0.0, 0.0))

        # Test IncompleteBendError on bad angle
        with self.assertRaises(IncompleteBendError):
            BendRecord(
                bend_id="BEND_ERR",
                feature_id="FEAT_ERR",
                parent_panel_id="PANEL_A",
                child_panel_id="PANEL_B",
                axis_origin=(0.0, 0.0, 0.0),
                axis_direction=(1.0, 0.0, 0.0),
                signed_angle=250.0,  # Out of range > 180
                radius=2.0,
                thickness=2.0
            )

        # Test IncompleteBendError on zero/negative thickness
        with self.assertRaises(IncompleteBendError):
            BendRecord(
                bend_id="BEND_ERR2",
                feature_id="FEAT_ERR2",
                parent_panel_id="PANEL_A",
                child_panel_id="PANEL_B",
                axis_origin=(0.0, 0.0, 0.0),
                axis_direction=(1.0, 0.0, 0.0),
                signed_angle=90.0,
                radius=2.0,
                thickness=-1.0  # Invalid non-positive thickness
            )

    def test_signed_angle_calculation(self):
        """Test geometric signed angle calculation with vector normals."""
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        extractor = FeatureExtractor(MockBackend())

        # Test +90 deg signed angle: n1=(0,0,1), n2=(0,1,0), bend_axis=(1,0,0)
        n1 = (0.0, 0.0, 1.0)
        n2 = (0.0, 1.0, 0.0)
        axis = (1.0, 0.0, 0.0)
        angle = extractor.calculate_signed_angle(n1, n2, axis)
        self.assertEqual(abs(angle), 90.0)

        # Test +45 deg signed angle
        n2_45 = (0.0, 0.7071, 0.7071)
        angle_45 = extractor.calculate_signed_angle(n1, n2_45, axis)
        self.assertAlmostEqual(abs(angle_45), 45.0, delta=0.1)

    def test_panel_graph_subtrees(self):
        """Test PanelGraph moving_subtree and fixed_set resolution for chain A - B - C - D."""
        graph = PanelGraph()
        
        # Register 4 panels: A, B, C, D
        graph.add_panel(Panel("PANEL_A"))
        graph.add_panel(Panel("PANEL_B"))
        graph.add_panel(Panel("PANEL_C"))
        graph.add_panel(Panel("PANEL_D"))

        # Connect chain with bends:
        # BEND_AB connects A and B
        # BEND_BC connects B and C
        # BEND_CD connects C and D
        graph.add_bend_connection("BEND_AB", "PANEL_A", "PANEL_B")
        graph.add_bend_connection("BEND_BC", "PANEL_B", "PANEL_C")
        graph.add_bend_connection("BEND_CD", "PANEL_C", "PANEL_D")

        # Test folding at BEND_BC:
        # parent = B, child = C
        # moving_subtree(BEND_BC) should be {PANEL_C, PANEL_D}
        # fixed_set(BEND_BC) should be {PANEL_A, PANEL_B}
        moving_bc = graph.moving_subtree("BEND_BC")
        fixed_bc = graph.fixed_set("BEND_BC")

        self.assertEqual(moving_bc, {"PANEL_C", "PANEL_D"})
        self.assertEqual(fixed_bc, {"PANEL_A", "PANEL_B"})

        # Test folding at BEND_CD:
        moving_cd = graph.moving_subtree("BEND_CD")
        fixed_cd = graph.fixed_set("BEND_CD")
        self.assertEqual(moving_cd, {"PANEL_D"})
        self.assertEqual(fixed_cd, {"PANEL_A", "PANEL_B", "PANEL_C"})


if __name__ == "__main__":
    unittest.main()
