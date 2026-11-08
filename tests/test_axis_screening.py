"""Headless safety tests for axis screening (no FreeCAD dependency)."""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.axis_screening import (
    active_bend_frame, axes_from_fold_state, group_parallel_bends,
    longitudinal_coordinate, longitudinal_probes,
)


class AxisScreeningTests(unittest.TestCase):
    def test_reversed_endpoints_share_group(self):
        axes = {"B1": ((0, 0, 0), (100, 0, 0)),
                "B2": ((50, 10, 0), (-50, 10, 0)),
                "B3": ((0, 0, 0), (0, 100, 0))}
        groups = group_parallel_bends(axes)
        self.assertEqual([g.bend_ids for g in groups], [("B1", "B2"), ("B3",)])

    def test_diagonal_group_not_forced_into_xy(self):
        axes = {"diag": ((0, 0, 0), (10, 10, 0)),
                "x": ((0, 0, 0), (10, 0, 0)),
                "y": ((0, 0, 0), (0, 10, 0))}
        self.assertEqual(len(group_parallel_bends(axes)), 3)

    def test_local_coordinates_follow_active_axis(self):
        axis = ((10, 10, 0), (10, 30, 0))
        self.assertEqual(longitudinal_coordinate((10, 20, 0), axis), 0)
        self.assertEqual(longitudinal_coordinate((10, 30, 0), axis), 10)
        self.assertEqual(active_bend_frame(axis)[2], (-1.0, 0.0, 0.0))

    def test_probe_tool_ends_and_panel_vertices(self):
        axis = ((0, 0, 0), (100, 0, 0))
        probes = longitudinal_probes(axis, tool_end_positions_mm=(-40, 40),
                                     panel_vertices_xyz=[(25, 12, 0)])
        self.assertTrue({-40.5, -40.0, -39.5, -25.5, -25.0, -24.5, 40.5}.issubset(probes))
        self.assertIn(0.0, probes)

    def test_probe_overflow_fails_instead_of_silently_dropping(self):
        with self.assertRaisesRegex(ValueError, "probe limit exceeded"):
            longitudinal_probes(((0, 0, 0), (100, 0, 0)), max_probes=1)

    def test_reject_invalid_or_missing_geometry(self):
        with self.assertRaisesRegex(ValueError, "zero-length"):
            group_parallel_bends({"B": ((0, 0, 0), (0, 0, 0))})
        with self.assertRaisesRegex(ValueError, "not in unfolded XY"):
            active_bend_frame(((0, 0, 0), (0, 0, 20)))
        with self.assertRaisesRegex(ValueError, "missing authoritative axis"):
            axes_from_fold_state(SimpleNamespace(bends={"B": SimpleNamespace()}))

    def test_read_fold_state_contract(self):
        state = SimpleNamespace(bends={
            "B": SimpleNamespace(axis_xyz=((0, 0, 0), (10, 0, 0)))
        })
        self.assertIn("B", axes_from_fold_state(state))


if __name__ == "__main__":
    unittest.main()
