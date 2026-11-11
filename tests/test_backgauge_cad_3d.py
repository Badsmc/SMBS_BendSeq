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
        self.assertEqual(adapter.stop_source_object, "Pad")
        self.assertEqual(adapter.stop_source_subelement, "Face6")
        self.assertGreater(adapter.stop_face_index, 0)
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

    def test_real_face6_reaches_exposed_external_panel_not_fixed_internal(self):
        """FreeCAD/OCC: 200mm stop body must not penetrate the whole sheet."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D

        app, part = self.app, self.part
        adapter = BackgaugeCAD3D(station_xm_mm=0.0)

        def panel_box(low_s, high_s):
            return part.makeBox(
                293.0, high_s-low_s, 1.5,
                app.Vector(-146.5, -high_s, 0.0),
            )

        panels = [
            ("panel:P04", panel_box(0, 11.2566)),
            ("panel:P05", panel_box(13.7699, 508.7699)),
            ("panel:P07", panel_box(530.0, 548.0)),
        ]
        state = SimpleNamespace(bends={
            "B06": SimpleNamespace(
                fixed_panel="P04",
                bend_zone=SimpleNamespace(thickness_mm=1.5),
            ),
        })
        with patch.object(adapter, "_panel_solids",
                          return_value=panels), patch.object(
            adapter, "_bend_solids", return_value=[],
        ):
            contact = adapter.solve_accessible_rear_contact(
                state, {}, object(), active_bend_id="B06",
            )
            sampled = adapter.check_phase(
                state, {}, object(), active_bend_id="B06",
                contact_s_mm=contact["contact_s_mm"],
                contact_z_mm=contact["contact_z_mm"],
            )
        self.assertEqual(contact["support_panel_id"], "P07")
        self.assertEqual(contact["source_datum"], "Pad.Face6")
        self.assertAlmostEqual(contact["contact_s_mm"], 548.0, places=3)
        self.assertFalse(contact["support_panel_matches_fixed"])
        self.assertFalse(contact["axis_limits_verified"])
        self.assertEqual(sampled["status"], "SAMPLED_CLEAR_OR_TOUCH")

    def test_bad_target_cannot_be_claimed_as_valid_stop(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import (
            BackgaugeCAD3D, BackgaugeCADError,
        )
        adapter = BackgaugeCAD3D()
        with self.assertRaises(BackgaugeCADError):
            adapter.positioned_shape(-50.0, 0.75)


if __name__ == "__main__":
    unittest.main()
