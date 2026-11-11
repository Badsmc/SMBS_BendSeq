"""FreeCAD-free contract tests for CAD datum contact height selection.

The mocked geometric kernel proves search control flow, NOT OCC correctness.
"""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D


class BBox:
    def __init__(self, xmin, xmax, ymin, ymax, zmin, zmax):
        self.XMin, self.XMax = xmin, xmax
        self.YMin, self.YMax = ymin, ymax
        self.ZMin, self.ZMax = zmin, zmax


class Normal:
    def dot(self, other):
        return 1.0


class FakeFace:
    ParameterRange = (0, 1, 0, 1)

    def __init__(self, depth, z):
        self.depth, self.z = depth, z
        self.BoundBox = BBox(-50, 50, -depth, -depth, z - 7, z + 7)

    def normalAt(self, *_args):
        return Normal()

    def distToShape(self, panel):
        p = panel.BoundBox
        reachable = (abs(self.depth + p.YMin) < 1e-6
                     and self.BoundBox.ZMax >= p.ZMin
                     and self.BoundBox.ZMin <= p.ZMax
                     and self.BoundBox.XMax >= p.XMin
                     and self.BoundBox.XMin <= p.XMax)
        return (0.0 if reachable else 99.0,)


class FakeGauge:
    def __init__(self, depth, z):
        self.Faces = (FakeFace(depth, z),)
        self.BoundBox = BBox(-50, 50, -depth - 5, -depth, z - 7, z + 7)

    def common(self, _solid):
        return SimpleNamespace(isNull=lambda: True, Volume=0.0)


class ContactIntervalTests(unittest.TestCase):
    def make_checker(self, panels):
        checker = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        checker.stop_face_index = 1
        checker.stop_source_object = "Pad"
        checker.stop_source_subelement = "Face6"
        checker.station_xm_mm = 0.0
        checker.second_station_xm_mm = None
        checker.app = SimpleNamespace(Vector=lambda *xyz: Normal())
        checker.positioned_shape = lambda s, z: FakeGauge(s, z)
        checker._panel_solids = lambda *_args: list(panels)
        checker._bend_solids = lambda *_args: []
        checker._trace = lambda *_args, **_kwargs: None
        return checker

    def test_tall_panel_center_unreachable_but_low_contact_found(self):
        panel = SimpleNamespace(BoundBox=BBox(-10, 10, -50, -49, 0, 300))
        checker = self.make_checker([("panel:P01", panel)])
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P01", moving_panel="P02",
                bend_zone=SimpleNamespace(thickness_mm=1.5)
            )
        })
        section = {"fixed_polygon": (
            (49, 0), (50, 0), (50, 300), (49, 300),
        ), "moving_polygon": (), "other_panel_polygons": ()}
        with patch.dict(sys.modules, {"Part": types.ModuleType("Part")}), patch(
            "smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section",
            return_value=section,
        ):
            result = checker.solve_accessible_rear_contact(
                state, {}, None, active_bend_id="B01"
            )
        self.assertEqual(result["contact_z_mm"], 0)
        self.assertEqual(result["contact_s_mm"], 50)
        self.assertTrue(result["candidate_z_interval_checked"])

    def test_rear_alternatives_are_recorded(self):
        panels = [
            ("panel:P01", SimpleNamespace(
                BoundBox=BBox(-10, 10, -50, -49, 0, 100))),
            ("panel:P02", SimpleNamespace(
                BoundBox=BBox(-10, 10, -30, -29, 0, 100))),
        ]
        checker = self.make_checker(panels)
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P01", moving_panel="P02",
                bend_zone=SimpleNamespace(thickness_mm=1.5)
            )
        })
        with patch.dict(sys.modules, {"Part": types.ModuleType("Part")}), patch(
            "smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section",
            return_value={},
        ):
            result = checker.solve_accessible_rear_contact(
                state, {}, None, active_bend_id="B01",
            )
        self.assertEqual(result["contact_s_mm"], 50)
        self.assertEqual(len(result["alternative_contacts"]), 1)
        self.assertEqual(result["alternative_contacts"][0]["contact_s_mm"], 30)


    def test_three_x_stations_find_contact_without_redundant_cad_bbox(self):
        panel = SimpleNamespace(BoundBox=BBox(-40, 40, -50, -49, 0, 100))
        checker = self.make_checker([("panel:P01", panel)])
        events, stations = [], []
        checker._trace = lambda event, **fields: events.append((event, fields))
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P01", moving_panel="P02",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            )
        })

        def section_at_x(*_args, **kwargs):
            x = float(kwargs["target_xm"])
            stations.append(x)
            poly = ((49, 0), (50, 0), (50, 100), (49, 100))
            return {
                "fixed_polygon": poly if x == -25.0 else (),
                "moving_polygon": (), "other_panel_polygons": (),
            }

        with patch.dict(sys.modules, {"Part": types.ModuleType("Part")}), patch(
            "smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section",
            side_effect=section_at_x,
        ):
            result = checker.solve_accessible_rear_contact(
                state, {}, None, active_bend_id="B01"
            )
        self.assertEqual(stations, [0.0, -25.0, 25.0])
        self.assertEqual(result["contact_s_mm"], 50.0)
        trials = [f for event, f in events
                  if event == "EXTERNAL_REAR_CONTACT_TRIAL"]
        self.assertTrue(trials)
        self.assertTrue(all(
            trial.get("candidate_depth_source") == "2D_SECTION"
            for trial in trials
        ))
        self.assertLessEqual(result["candidate_occ_trials"], 3)

    def test_fallback_only_when_section_cannot_locate_rear_contact(self):
        panel = SimpleNamespace(BoundBox=BBox(-10, 10, -50, -49, 0, 100))
        checker = self.make_checker([("panel:P01", panel)])
        events = []
        checker._trace = lambda event, **fields: events.append((event, fields))
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P01", moving_panel="P02",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            )
        })
        with patch.dict(sys.modules, {"Part": types.ModuleType("Part")}), patch(
            "smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section",
            return_value={},
        ):
            result = checker.solve_accessible_rear_contact(
                state, {}, None, active_bend_id="B01"
            )
        self.assertEqual(result["candidate_occ_trials"], 1)
        sources = [fields["candidate_depth_source"]
                   for event, fields in events
                   if event == "EXTERNAL_REAR_CONTACT_TRIAL"]
        self.assertEqual(sources, ["CAD_BBOX_FALLBACK"])

    def test_failed_section_proposal_never_claims_proven_impossible(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCADError
        panel = SimpleNamespace(BoundBox=BBox(-10, 10, -50, -49, 0, 100))
        checker = self.make_checker([("panel:P01", panel)])
        state = SimpleNamespace(bends={
            "B01": SimpleNamespace(
                fixed_panel="P01", moving_panel="P02",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            )
        })
        # The section proposes S=40, but the real CAD panel is at S=50.
        # Do not promote its AABB fallback or prove a global impossibility.
        wrong_section = {
            "fixed_polygon": ((39, 0), (40, 0), (40, 100), (39, 100)),
            "moving_polygon": (), "other_panel_polygons": (),
        }
        with patch.dict(sys.modules, {"Part": types.ModuleType("Part")}), patch(
            "smbs_core.kinematics2d.machine_section_3d.compute_machine_cutting_plane_section",
            return_value=wrong_section,
        ), self.assertRaisesRegex(
            BackgaugeCADError, "CAD_CONTACT_SEARCH_INCOMPLETE"
        ):
            checker.solve_accessible_rear_contact(
                state, {}, None, active_bend_id="B01"
            )


if __name__ == "__main__":
    unittest.main()
