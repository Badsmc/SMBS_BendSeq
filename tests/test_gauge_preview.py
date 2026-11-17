import unittest

from smbs_core.kinematics2d.backgauge_2d import BackgaugeModel2D, BackgaugePosition2D
from smbs_core.kinematics2d.gauge_preview import (
    gauge_rear_envelope_from_faces,
    visual_gauge_target,
)
from smbs_core.kinematics2d.preview_mesh_3d import PreviewFace3D


class TestGaugePreview(unittest.TestCase):
    def test_sloped_edge_is_measured_at_both_finger_stations(self):
        face = PreviewFace3D(
            ((25, -20, 0), (65, 20, 0), (65, 20, 2), (25, -20, 2)),
            "panel_edge", "P01",
        )
        edges = tuple(gauge_rear_envelope_from_faces((face,), station)
                      for station in (-10, 10, 30))
        self.assertEqual(edges, ((35, 1), (55, 1), None))

        model = BackgaugeModel2D(10, 10, {}, automatic=True, edge_clearance_mm=1)
        target, note = visual_gauge_target(model, "B01", edges[:2])
        self.assertEqual(target, BackgaugePosition2D(56, 1))
        self.assertIn("21.00 мм", note)
        target, note = visual_gauge_target(model, "B01", edges[1:])
        self.assertEqual(target, BackgaugePosition2D(56, 1))
        self.assertIn("вне контура", note)
        target, _ = visual_gauge_target(model, "B01", edges[2:])
        self.assertIsNone(target)

    def test_manual_position_keeps_same_face_distance_from_bend(self):
        model = BackgaugeModel2D(10, 10, {"B01": BackgaugePosition2D(100, 2)})
        self.assertEqual(visual_gauge_target(model, "B01", ()),
                         (BackgaugePosition2D(95, 2), ""))
