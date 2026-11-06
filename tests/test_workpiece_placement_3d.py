"""Tests for WorkpiecePlacement3D - Spatial 3D Placement and Non-Parallel Bend Transitions."""
import math
import unittest

from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.workpiece_placement_3d import (
    PlacementTransform3D,
    WorkpiecePlacement3D,
    _mat_det,
)


class WorkpiecePlacement3DTests(unittest.TestCase):
    """Test suite for WorkpiecePlacement3D spatial placement contracts."""

    def setUp(self):
        # Build test part with 2 perpendicular bends B01 (along X) and B02 (along Y)
        # B01 axis: (0, 50, 0) -> (100, 50, 0)
        # B02 axis: (50, 50, 0) -> (50, 150, 0)
        p1 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        p2 = bend_parameters(90.0, 1.0, 1.5, 0.4)
        z1 = make_bend_zone("B01", ((0.0, 50.0), (100.0, 50.0)), p1)
        z2 = make_bend_zone("B02", ((50.0, 50.0), (50.0, 150.0)), p2)

        self.graph = PanelGraph(
            ("P00", "P01", "P02"),
            (
                GraphEdge("B01", "P00", "P01", 90.0, ((0.0, 50.0, 0.0), (100.0, 50.0, 0.0)), z1),
                GraphEdge("B02", "P00", "P02", 90.0, ((50.0, 50.0, 0.0), (50.0, 150.0, 0.0)), z2),
            ),
        )
        self.fold_state = FoldState(self.graph, "P00")
        self.verts = {
            "P00": ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)),
            "P01": ((0.0, 52.0), (100.0, 52.0), (100.0, 100.0), (0.0, 100.0)),
            "P02": ((52.0, 50.0), (100.0, 50.0), (100.0, 150.0), (52.0, 150.0)),
        }

    def test_d634_arc_center_alignment_is_opt_in_and_default_unchanged(self):
        state = self.fold_state
        original = WorkpiecePlacement3D.compute_placement(
            state, "B01", support_plane_zm=0.0, align_bisector=True,
        )
        explicit_default = WorkpiecePlacement3D.compute_placement(
            state, "B01", support_plane_zm=0.0, align_bisector=True,
            align_bend_arc_center=False,
        )
        self.assertEqual(original, explicit_default)
        centered = WorkpiecePlacement3D.compute_placement(
            state, "B01", support_plane_zm=0.0, align_bisector=True,
            align_bend_arc_center=True,
        )
        self.assertAlmostEqual(_mat_det(centered.rotation), 1.0, places=6)
        self.assertNotEqual(centered.translation, original.translation)

    def test_placement_is_proper_rigid_isometry(self):
        """Verify placement rotation matrix is orthogonal with det(R) == +1.0."""
        for bid in ("B01", "B02"):
            placement = WorkpiecePlacement3D.compute_placement(self.fold_state, bid)
            det = _mat_det(placement.rotation)
            self.assertAlmostEqual(det, 1.0, places=9)

            # Check orthogonality R * R^T == I
            R = placement.rotation
            for i in range(3):
                for j in range(3):
                    dot = sum(R[i][k] * R[j][k] for k in range(3))
                    expected = 1.0 if i == j else 0.0
                    self.assertAlmostEqual(dot, expected, places=9)

    def test_active_bend_axis_strictly_maps_to_machine_xm_axis(self):
        """Active bend line must align with X_m (Y_m=0, Z_m=0 at die support)."""
        for bid in ("B01", "B02"):
            placement = WorkpiecePlacement3D.compute_placement(
                self.fold_state, bid, desired_midpoint_xm=0.0, support_plane_zm=-1.5
            )
            p0, p1, _ = WorkpiecePlacement3D.get_world_bend_axis(self.fold_state, bid)
            p0_m = placement.apply(p0)
            p1_m = placement.apply(p1)

            # Both endpoints must lie strictly at Y_m = 0, Z_m = -1.5
            self.assertAlmostEqual(p0_m[1], 0.0, places=9)
            self.assertAlmostEqual(p0_m[2], -1.5, places=9)
            self.assertAlmostEqual(p1_m[1], 0.0, places=9)
            self.assertAlmostEqual(p1_m[2], -1.5, places=9)

            # Midpoint X_m must be at desired_midpoint_xm
            mid_xm = 0.5 * (p0_m[0] + p1_m[0])
            self.assertAlmostEqual(mid_xm, 0.0, places=9)

            # Axis direction vector in Machine space must point along X_m
            axis_len = math.dist(p0_m, p1_m)
            self.assertAlmostEqual(abs(p1_m[0] - p0_m[0]), axis_len, places=9)

    def test_nonparallel_consecutive_bends_continuous_point_distance(self):
        """Switching from B01 to perpendicular B02 preserves distances between physical points."""
        # Bend B01 by 90 degrees
        self.fold_state.set_angle("B01", 90.0)

        # Placement for B01
        pl_b01 = WorkpiecePlacement3D.compute_placement(self.fold_state, "B01")
        pts_p00_b01 = WorkpiecePlacement3D.get_panel_vertices_machine(
            self.fold_state, pl_b01, "P00", self.verts["P00"]
        )
        dist_b01 = math.dist(pts_p00_b01[0], pts_p00_b01[1])

        # Placement for B02 (perpendicular axis)
        pl_b02 = WorkpiecePlacement3D.compute_placement(self.fold_state, "B02")
        pts_p00_b02 = WorkpiecePlacement3D.get_panel_vertices_machine(
            self.fold_state, pl_b02, "P00", self.verts["P00"]
        )
        dist_b02 = math.dist(pts_p00_b02[0], pts_p00_b02[1])

        # Internal geometry of P00 is 100% invariant between machine placements
        self.assertAlmostEqual(dist_b01, dist_b02, places=9)

        # Relative spatial transition between B01 and B02 placements
        # T_transit = pl_b02 * pl_b01.inverse()
        t_transit = pl_b01.inverse().then(pl_b02)
        det_transit = _mat_det(t_transit.rotation)
        self.assertAlmostEqual(det_transit, 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
