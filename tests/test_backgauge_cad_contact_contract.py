"""Headless contracts for CAD first-contact orchestration.

FreeCAD OCC geometric validation lives in test_backgauge_cad_3d.py and
must be run under FreeCAD; these tests verify the station and setup wiring.
"""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.backgauge_2d import (
    BackgaugeModel2D, BackgaugePosition2D,
)
from smbs_core.kinematics2d.backgauge_cad_3d import (
    BackgaugeCAD3D, BackgaugeCADError,
)


class CADFirstContactContractTests(TestCase):
    def test_contact_solver_uses_finger_station_and_does_not_move_bend_pose(self):
        model = BackgaugeModel2D(
            10.0, 10.0, {}, automatic=True,
            cad_geometry_enabled=True,
            cad_station_xm_mm=50.0,
        )
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                bend_zone=SimpleNamespace(outer_radius_mm=2.5)
            )
        })
        adapter = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        adapter.station_xm_mm = 50.0
        adapter.second_station_xm_mm = None
        adapter.stop_face_index = 3
        expected = {
            "contact_s_mm": 127.5, "contact_z_mm": 0.75,
            "hint_s_mm": 120.01, "shift_rearward_mm": 7.49,
            "face3_is_contact": True,
            "actual_faces": [3], "note": "CONTACT_FACE3",
        }
        target = BackgaugePosition2D(125.01, 0.75)
        with patch.object(
            BackgaugeModel2D, "resolve_for_state",
            return_value=(target, 120.0, "AUTO")
        ) as resolve, patch(
            "smbs_core.kinematics2d.workpiece_placement_3d."
            "WorkpiecePlacement3D.compute_placement",
            return_value=object(),
        ) as placement, patch.object(
            adapter, "solve_first_contact", return_value=expected,
        ) as contact:
            selected, depth, source, result = (
                adapter.resolve_contact_for_bend(
                    state, {}, "B01", model,
                    desired_midpoint_xm=0.0,
                    tolerance_mm=0.02,
                )
            )
        self.assertEqual(resolve.call_args.kwargs["cutting_plane_xm"], 50.0)
        self.assertEqual(
            placement.call_args.kwargs["desired_midpoint_xm"], 0.0
        )
        self.assertAlmostEqual(contact.call_args.kwargs["hint_s_mm"], 120.01)
        self.assertEqual(selected.s_mm, 132.5)
        self.assertEqual(depth, 127.5)
        self.assertEqual(result["actual_faces"], [3])
        self.assertIn("CAD_FIRST_CONTACT", source)

    def test_different_edge_depths_for_two_fingers_fail_closed(self):
        model = BackgaugeModel2D(
            10, 10, {}, automatic=True,
            cad_geometry_enabled=True,
            cad_station_xm_mm=-200.0,
            cad_second_station_xm_mm=200.0,
        )
        state = SimpleNamespace(bends={"B01": object()})
        adapter = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        adapter.station_xm_mm = -200.0
        adapter.second_station_xm_mm = 200.0
        with patch.object(
            BackgaugeModel2D, "resolve_for_state",
            side_effect=[
                (BackgaugePosition2D(125, .75), 120, "AUTO"),
                (BackgaugePosition2D(130, .75), 125, "AUTO"),
            ],
        ), self.assertRaisesRegex(
            BackgaugeCADError, "INDEPENDENT_TARGETS"
        ):
            adapter.resolve_contact_for_bend(state, {}, "B01", model)
