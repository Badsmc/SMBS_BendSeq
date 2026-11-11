"""Headless geometry contracts for external rear-facing CAD gauge contact.

These test the candidate selection and OCC call ordering with small fake
solids. They do NOT validate the real FreeCAD Part kernel or machine axes.
"""
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from smbs_core.kinematics2d.backgauge_cad_3d import (
    BackgaugeCAD3D, BackgaugeCADError,
)


class Vector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z
    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z


class Bounds:
    def __init__(self, start_s, end_s, x0=-60, x1=60, z0=0, z1=1.5):
        self.XMin, self.XMax = float(x0), float(x1)
        self.YMin, self.YMax = -float(end_s), -float(start_s)
        self.ZMin, self.ZMax = float(z0), float(z1)


class Solid:
    def __init__(self, start_s, end_s, x0=-60, x1=60, z0=0, z1=1.5):
        self.BoundBox = Bounds(start_s, end_s, x0, x1, z0, z1)
        self.Volume = (end_s-start_s) * (x1-x0) * (z1-z0)
        self.start_s = float(start_s)
        self.end_s = float(end_s)
    def common(self, other):
        a, b = self.BoundBox, other.BoundBox
        xd = max(0., min(a.XMax, b.XMax)-max(a.XMin, b.XMin))
        yd = max(0., min(a.YMax, b.YMax)-max(a.YMin, b.YMin))
        zd = max(0., min(a.ZMax, b.ZMax)-max(a.ZMin, b.ZMin))
        v = xd*yd*zd
        return SimpleNamespace(isNull=lambda: v == 0., Volume=v)


class Face:
    ParameterRange = (0., 1., 0., 1.)
    def __init__(self, stop_s, xmin=-50.6433, xmax=49.3567):
        self.stop_s = stop_s
        self.xmin, self.xmax = xmin, xmax
        self.BoundBox = Bounds(
            stop_s, stop_s, x0=xmin, x1=xmax,
            z0=-6.00308, z1=7.99692,
        )
    def normalAt(self, u, v):
        return Vector(0, 1, 0)
    def distToShape(self, solid):
        b = solid.BoundBox
        dx = max(0., b.XMin-self.xmax, self.xmin-b.XMax)
        ds = abs(float(self.stop_s)-float(solid.end_s))
        return (max(dx, ds), None, None)


class Gauge(Solid):
    def __init__(self, stop_s):
        super().__init__(
            stop_s, stop_s+200,
            x0=-50.6433, x1=49.3567,
            z0=-6.00308, z1=7.99692,
        )
        self.Faces = [Face(stop_s) for _ in range(6)]


class ExternalRearCADContactTests(unittest.TestCase):
    def adapter(self, panels, zones=()):
        adapter = BackgaugeCAD3D.__new__(BackgaugeCAD3D)
        adapter.app = SimpleNamespace(Vector=Vector)
        adapter.stop_face_index = 6
        adapter.stop_source_object = "Pad"
        adapter.stop_source_subelement = "Face6"
        adapter.station_xm_mm = 0.0
        adapter.second_station_xm_mm = None
        adapter._placement_cache = {}
        return adapter

    def run_contact(self, panels, zones=()):
        adapter = self.adapter(panels, zones)
        state = SimpleNamespace(bends={
            "B06": SimpleNamespace(
                fixed_panel="P04",
                moving_panel="P05",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            )
        })
        with patch.dict(sys.modules, {"Part": Mock()}), patch.object(
            adapter, "_panel_solids", return_value=panels,
        ), patch.object(
            adapter, "_bend_solids", return_value=list(zones),
        ), patch.object(
            adapter, "positioned_shapes",
            side_effect=lambda s, z: (Gauge(s),),
        ):
            return adapter.solve_accessible_rear_contact(
                state, {}, object(), active_bend_id="B06",
            )

    def test_rear_external_panel_selected_instead_of_kinematic_fixed_panel(self):
        result = self.run_contact([
            ("panel:P04", Solid(0, 11.2566)),
            ("panel:P05", Solid(13.77, 508.77)),
            ("panel:P07", Solid(531, 548)),
        ])
        self.assertEqual(result["support_panel_id"], "P07")
        self.assertEqual(result["contact_s_mm"], 548)
        self.assertEqual(result["contact_station_xm_mm"], 0.0)
        self.assertTrue(result["selected_datum_is_contact"])
        self.assertFalse(result["support_panel_matches_fixed"])
        self.assertFalse(result["axis_limits_verified"])

    def test_does_not_accept_rear_panel_outside_physical_face_x_span(self):
        result = self.run_contact([
            ("panel:P04", Solid(0, 11.2566)),
            ("panel:P05", Solid(13.77, 508.77)),
            ("panel:P07", Solid(531, 548, x0=100, x1=120)),
        ])
        self.assertEqual(result["support_panel_id"], "P05")
        self.assertEqual(result["contact_s_mm"], 508.77)

    def test_real_intervening_bend_zone_remains_blocking(self):
        with self.assertRaisesRegex(
            BackgaugeCADError, "CAD_CONTACT_SEARCH_INCOMPLETE",
        ):
            self.run_contact([
                ("panel:P04", Solid(0, 11.2566)),
                ("panel:P05", Solid(13.77, 508.77)),
            ], [
                ("bend_zone:B10", Solid(509, 520)),
            ])


if __name__ == "__main__":
    unittest.main()
