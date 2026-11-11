"""FreeCAD runtime checks for actual Backgauge.FCStd 3D integration.

The tests skip when FreeCAD/OCC is absent (e.g. ordinary headless Python CI).
They deliberately make no claim about complete machine/swept-path safety.
"""
from __future__ import annotations

import unittest


class BackgaugeRealCADTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import FreeCAD
            import Part
        except ImportError:
            raise unittest.SkipTest("FreeCAD/Part runtime required")
        cls.app = FreeCAD
        cls.part = Part

    def test_real_stop_face_aligns_to_machine_right(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D
        adapter = BackgaugeCAD3D(station_xm_mm=15.0)
        self.assertEqual(adapter.stop_face_index, 3)
        shape = adapter.positioned_shape(150.0, 0.75)
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        contact_point = self.app.Vector(15.0, -150.0, 0.75)
        face = shape.Faces[adapter.stop_face_index - 1]
        dist, _, _ = face.distToShape(self.part.Vertex(contact_point))
        self.assertLessEqual(dist, 0.05)
        u_min, u_max, v_min, v_max = face.ParameterRange
        n = face.normalAt(
            (u_min+u_max)/2, (v_min+v_max)/2
        )
        self.assertGreater(n.dot(self.app.Vector(0, 1, 0)), 0.999)

    def test_cad_stop_does_not_mutate_existing_freecad_document(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D
        previous = self.app.newDocument("SMBS_CAD_Stop_Test")
        try:
            self.app.setActiveDocument(previous.Name)
            adapter = BackgaugeCAD3D()
            self.assertEqual(self.app.ActiveDocument.Name, previous.Name)
            a = adapter.positioned_shape(120.0, 0.75)
            b = adapter.positioned_shape(120.0, 0.75)
            self.assertIs(a, b)
            self.assertTrue(a.isValid())
        finally:
            self.app.closeDocument(previous.Name)

    def test_real_cad_volume_checker_on_freecad_fold_state(self):
        """Runtime smoke test: real OCC gauge vs real FoldState panel/zone."""
        from smbs_core.kinematics2d.bend_zone import (
            bend_parameters, make_bend_zone,
        )
        from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
        from smbs_core.kinematics2d.panel_kinematics import FoldState
        from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D
        params = bend_parameters(90.0, 1.0, 1.5, 0.4)
        zone = make_bend_zone(
            "B01", ((0.0, 50.0), (100.0, 50.0)), params
        )
        edge = GraphEdge(
            "B01", "P00", "P01", 90.0,
            ((0.0, 50.0, 0.0), (100.0, 50.0, 0.0)),
            zone,
        )
        state = FoldState(PanelGraph(("P00", "P01"), (edge,)), "P00")
        vertices = {
            "P00": ((0., 0.), (100., 0.), (100., 50.), (0., 50.)),
            "P01": ((0., 52.), (100., 52.), (100., 100.), (0., 100.)),
        }
        pose = WorkpiecePlacement3D.compute_placement(
            state, "B01", support_plane_zm=zone.outer_radius_mm,
            align_bisector=False, align_bend_arc_center=True,
            rear_panel_vertices_xy=vertices,
        )
        adapter = BackgaugeCAD3D()
        verdict = adapter.check_phase(
            state, vertices, pose, active_bend_id="B01",
            contact_s_mm=45.0, contact_z_mm=0.75,
            tolerance_mm=0.02,
        )
        self.assertIn(
            verdict["status"], ("SAMPLED_CLEAR_OR_TOUCH", "INTERFERENCE"),
        )
        self.assertIn("evidence", verdict)

    def test_two_explicit_cad_stations_and_overlap_rejection(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import (
            BackgaugeCAD3D, BackgaugeCADError,
        )
        separated = BackgaugeCAD3D(
            station_xm_mm=-200.0, second_station_xm_mm=200.0
        )
        shapes = separated.positioned_shapes(120.0, 0.75)
        self.assertEqual(len(shapes), 2)
        self.assertTrue(all(shape.isValid() for shape in shapes))
        overlapping = BackgaugeCAD3D(
            station_xm_mm=0.0, second_station_xm_mm=1.0
        )
        with self.assertRaisesRegex(BackgaugeCADError, "intersect") as ctx:
            overlapping.positioned_shapes(120.0, 0.75)
        self.assertIn("X1=0.00 mm", str(ctx.exception))
        self.assertIn("X2=1.00 mm", str(ctx.exception))
        self.assertIn("actual CAD X-width=", str(ctx.exception))

    def test_bad_target_cannot_be_claimed_as_valid_stop(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import (
            BackgaugeCAD3D, BackgaugeCADError,
        )
        adapter = BackgaugeCAD3D()
        with self.assertRaises(BackgaugeCADError):
            adapter.positioned_shape(-50.0, 0.75)


if __name__ == "__main__":
    unittest.main()
