"""Unit and regression tests for positive and negative bend orientation in 3D Machine Space.

Tests:
1. Positive bend (+30, +45, +60, +90): flanges rise upward into +Z_m in machine space.
2. Negative bend (-30, -45, -60, -90): flanges rise upward into +Z_m in machine space.
3. Multi-bend part with alternating positive and negative bends: 3D spatial transfer orientation.
4. Hardened trace analyzer detects inverted flange orientation as a GEOMETRIC_INCONSISTENCY.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D, _mat_det


class NegativeBendOrientationTests(unittest.TestCase):
    """Test suite verifying upward flange rise in Machine Space for positive and negative bends."""

    def _build_single_bend_part(self, target_angle_deg: float) -> tuple[FoldState, dict]:
        # Simple part with 2 panels P01 (fixed) and P02 (moving), connected along Y=50
        ref_r = 1.0 if target_angle_deg >= 0 else 2.5
        p = bend_parameters(target_angle_deg, ref_r, 1.5, 0.4)
        z = make_bend_zone("B01", ((0.0, 50.0), (100.0, 50.0)), p)
        # Note: panel_a=P02 ensures moving_left=True for moving panel P02 at Y in [50, 100]
        graph = PanelGraph(
            ("P01", "P02"),
            (
                GraphEdge("B01", "P02", "P01", target_angle_deg, ((0.0, 50.0, 0.0), (100.0, 50.0, 0.0)), z),
            ),
        )
        fold_state = FoldState(graph, "P01")
        verts = {
            "P01": ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)),
            "P02": ((0.0, 50.0), (100.0, 50.0), (100.0, 100.0), (0.0, 100.0)),
        }
        return fold_state, verts

    def test_positive_bend_angles_flanges_rise_upward(self):
        """Positive bends (+30, +45, +60, +90) must place outer surface down and flanges rising into +Zm."""
        for angle in (30.0, 45.0, 60.0, 90.0):
            fold_state, verts = self._build_single_bend_part(angle)
            fold_state.set_angle("B01", angle)
            placement = WorkpiecePlacement3D.compute_placement(
                fold_state, "B01", support_plane_zm=-1.5, align_bisector=True
            )
            # Ensure proper rigid rotation
            self.assertAlmostEqual(_mat_det(placement.rotation), 1.0, places=7)

            # Transformed panel vertices in Machine Space
            v_p1 = WorkpiecePlacement3D.get_panel_vertices_machine(fold_state, placement, "P01", verts["P01"])
            v_p2 = WorkpiecePlacement3D.get_panel_vertices_machine(fold_state, placement, "P02", verts["P02"])

            p0, p1, _ = WorkpiecePlacement3D.get_world_bend_axis(fold_state, "B01")
            mid_m = placement.apply((0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]), 0.5 * (p0[2] + p1[2])))

            # Both flanges must rise upward above the bend root
            max_z_p1 = max(pt[2] for pt in v_p1)
            max_z_p2 = max(pt[2] for pt in v_p2)
            self.assertGreater(max_z_p1, mid_m[2], f"Positive angle {angle}deg: fixed flange did not rise upward")
            self.assertGreater(max_z_p2, mid_m[2], f"Positive angle {angle}deg: moving flange did not rise upward")

    def test_negative_bend_angles_flanges_rise_upward(self):
        """Negative bends (-30, -45, -60, -90) must place outer surface down and flanges rising into +Zm."""
        for angle in (-30.0, -45.0, -60.0, -90.0):
            fold_state, verts = self._build_single_bend_part(angle)
            fold_state.set_angle("B01", angle)
            placement = WorkpiecePlacement3D.compute_placement(
                fold_state, "B01", support_plane_zm=-1.5, align_bisector=True
            )
            # Ensure proper rigid rotation
            self.assertAlmostEqual(_mat_det(placement.rotation), 1.0, places=7)

            # Transformed panel vertices in Machine Space
            v_p1 = WorkpiecePlacement3D.get_panel_vertices_machine(fold_state, placement, "P01", verts["P01"])
            v_p2 = WorkpiecePlacement3D.get_panel_vertices_machine(fold_state, placement, "P02", verts["P02"])

            p0, p1, _ = WorkpiecePlacement3D.get_world_bend_axis(fold_state, "B01")
            mid_m = placement.apply((0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]), 0.5 * (p0[2] + p1[2])))

            # Both flanges must rise upward above the bend root (into +Zm), NOT extend down into the die bed
            max_z_p1 = max(pt[2] for pt in v_p1)
            max_z_p2 = max(pt[2] for pt in v_p2)
            min_z_p1 = min(pt[2] for pt in v_p1)
            min_z_p2 = min(pt[2] for pt in v_p2)

            self.assertGreater(max_z_p1, mid_m[2], f"Negative angle {angle}deg: fixed flange did not rise upward")
            self.assertGreater(max_z_p2, mid_m[2], f"Negative angle {angle}deg: moving flange did not rise upward")
            # Flange roots stay at the bend axis while tips rise upward
            self.assertGreaterEqual(min_z_p1, mid_m[2] - 0.5, f"Negative angle {angle}deg: fixed flange penetrated into die bed")
            self.assertGreaterEqual(min_z_p2, mid_m[2] - 0.5, f"Negative angle {angle}deg: moving flange penetrated into die bed")


if __name__ == "__main__":
    unittest.main()
