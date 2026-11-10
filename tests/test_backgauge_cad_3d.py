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

    def test_bad_target_cannot_be_claimed_as_valid_stop(self):
        from smbs_core.kinematics2d.backgauge_cad_3d import (
            BackgaugeCAD3D, BackgaugeCADError,
        )
        adapter = BackgaugeCAD3D()
        with self.assertRaises(BackgaugeCADError):
            adapter.positioned_shape(-50.0, 0.75)


if __name__ == "__main__":
    unittest.main()
