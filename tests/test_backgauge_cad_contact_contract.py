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

    def test_contact_brackets_fixed_panel_not_unrelated_rear_panel(self):
        """Other sheet material cannot push the intended stop hundreds of mm.

        Synthetic solids model Y extents only. We test search orchestration,
        not FreeCAD/OCC robustness or real curved-face collision.
        """
        import sys
        from unittest.mock import Mock
        from types import SimpleNamespace

        class Box:
            XMin, XMax = 0., 100.
            ZMin, ZMax = 0., 10.
            def __init__(self, s_min, s_max):
                # Machine Y = -s.
                self.YMin = -float(s_max)
                self.YMax = -float(s_min)

        class Solid:
            def __init__(self, s_min, s_max):
                self.s_min, self.s_max = s_min, s_max
                self.BoundBox = Box(s_min, s_max)

            def common(self, other):
                width = max(0., min(self.s_max, other.s_max)
                            - max(self.s_min, other.s_min))
                return SimpleNamespace(isNull=lambda: width == 0.,
                                       Volume=width)

            def distToShape(self, other):
                dist = max(0., other.s_min - self.s_max,
                           self.s_min - other.s_max)
                return (dist, None, None)

        adapter = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        fixed = Solid(30., 36.)
        unrelated = Solid(40., 45.)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P04",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            )
        })
        def stop_at(s, z):
            # Finger reaches 10 mm ahead of nominal stop face.
            return (Solid(s-10., s),)
        # Near contact with fixed panel: gauge s ~46. But other panel at
        # s=40-45 is in the way. Never retreat to s~55 and call it safe.
        with patch.dict(sys.modules, {"Part": Mock()}), patch.object(
            adapter, "_panel_solids",
            return_value=[("panel:P04", fixed), ("panel:P05", unrelated)],
        ), patch.object(
            adapter, "_bend_solids", return_value=[],
        ), patch.object(
            adapter, "positioned_shapes", side_effect=stop_at,
        ), patch.object(
            adapter, "_qualified_contact",
            return_value={"status": "FIRST_NONPENETRATING_CAD_FACE_CONTACT_SAMPLED"},
        ) as qualified:
            with self.assertRaisesRegex(
                BackgaugeCADError, "OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT"
            ) as captured:
                adapter.solve_first_contact(
                    state, {}, object(), active_bend_id="B01",
                    hint_s_mm=36.0, contact_z_mm=0.75,
                )
        self.assertIn("obstructing_component=panel:P05", str(captured.exception))
        self.assertIn("fixed=panel:P04", str(captured.exception))
        self.assertNotIn("stop_s_mm=563", str(captured.exception))
        qualified.assert_not_called()

    def test_clear_fixed_panel_contact_does_not_get_pushed_back(self):
        import sys
        from unittest.mock import Mock
        from types import SimpleNamespace

        class Box:
            XMin, XMax = 0., 100.
            ZMin, ZMax = 0., 10.
            def __init__(self, low, high):
                self.YMin, self.YMax = -high, -low

        class Solid:
            def __init__(self, low, high):
                self.low, self.high = low, high
                self.BoundBox = Box(low, high)
            def common(self, other):
                width = max(0., min(self.high, other.high)
                            - max(self.low, other.low))
                return SimpleNamespace(isNull=lambda: width == 0.,
                                       Volume=width)
            def distToShape(self, other):
                return (max(0., other.low-self.high,
                            self.low-other.high), None, None)

        fixed, far_sheet = Solid(30., 36.), Solid(500., 520.)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(fixed_panel="P04",
                                    bend_zone=SimpleNamespace(thickness_mm=1.5))
        })
        adapter = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        def stop_at(s, z):
            return (Solid(s-10., s),)
        with patch.dict(sys.modules, {"Part": Mock()}), patch.object(
            adapter, "_panel_solids",
            return_value=[("panel:P04", fixed), ("panel:P05", far_sheet)],
        ), patch.object(adapter, "_bend_solids", return_value=[]), patch.object(
            adapter, "positioned_shapes", side_effect=stop_at,
        ), patch.object(adapter, "_qualified_contact") as qualified:
            qualified.side_effect = lambda shapes, fixed, s, *args: {
                "contact_s_mm": s
            }
            result = adapter.solve_first_contact(
                state, {}, object(), active_bend_id="B01",
                hint_s_mm=36.0, contact_z_mm=0.75,
            )
        self.assertLess(result["contact_s_mm"], 50.)
        self.assertGreaterEqual(result["contact_s_mm"], 46.)

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
