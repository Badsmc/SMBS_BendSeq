"""Phase 5C contact oracle regression: clear, touch, interference, holes.

These tests establish 2D cross-section classifications only. They do not
certify a full 3D machine sequence or a physically feasible punch stroke.
"""
import unittest

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactGeometryError,
    ContactStatus,
    check_machine_section,
    classify_polygon_contact,
    check_sheet_tooling_preview,
)


def rect(x0,y0,x1,y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1))


class ContactOracle2DTests(unittest.TestCase):
    def test_separated_polygons_clear(self):
        status, reason=classify_polygon_contact(
            rect(-2,1,-1,2), rect(-2,-2,2,0)
        )
        self.assertEqual(status,ContactStatus.CLEAR)
        self.assertEqual(reason,"separated_bounding_boxes")

    def test_boundary_only_touch_is_not_collision(self):
        status,_=classify_polygon_contact(
            rect(-1,0,1,1),rect(-1,-1,1,0)
        )
        self.assertEqual(status,ContactStatus.TOUCH)

    def test_positive_area_overlap_is_interference(self):
        status,_=classify_polygon_contact(
            rect(-1,-0.25,1,0.75),rect(-2,-2,2,0)
        )
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_contained_sheet_inside_tool_is_interference(self):
        status,_=classify_polygon_contact(
            rect(-0.5,-0.5,0.5,0.5),rect(-2,-2,2,2)
        )
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_exactly_coincident_polygons_are_interference_not_touch(self):
        status,_ = classify_polygon_contact(
            rect(-2,-2,2,2),rect(-2,-2,2,2)
        )
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_shared_edge_on_opposite_sides_is_touch(self):
        status,_ = classify_polygon_contact(
            rect(-2,0,2,2),rect(-2,-2,2,0)
        )
        self.assertEqual(status,ContactStatus.TOUCH)

    def test_tool_inside_sheet_is_interference(self):
        status,_=classify_polygon_contact(
            rect(-4,-4,4,4),rect(-1,-1,1,1)
        )
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_sheet_inside_tool_open_cavity_is_clear(self):
        status,_=classify_polygon_contact(
            rect(-0.5,-0.5,0.5,0.5),
            rect(-5,-5,5,5),
            (rect(-2,-2,2,2),)
        )
        self.assertEqual(status,ContactStatus.CLEAR)

    def test_entering_tool_from_cavity_is_interference(self):
        status,_=classify_polygon_contact(
            rect(1.5,-0.25,2.5,0.25),
            rect(-5,-5,5,5),
            (rect(-2,-2,2,2),)
        )
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_triangle_crossing_detected_without_vertex_containment(self):
        a=((-3,0),(3,0),(0,3))
        b=((-3,2),(3,2),(0,-1))
        status,_=classify_polygon_contact(a,b)
        self.assertEqual(status,ContactStatus.INTERFERENCE)

    def test_dense_tool_contour_far_edges_preserve_contact(self):
        # Many distant vertices with only a small portion near the sheet.
        # Guards the segment-AABB and witness broad-phase optimization.
        from math import cos, sin, pi
        ring = tuple((100.0 * cos(2*pi*i/256),
                      100.0 * sin(2*pi*i/256)) for i in range(256))
        inside, _ = classify_polygon_contact(
            rect(-1.0, -1.0, 1.0, 1.0), ring
        )
        outside, _ = classify_polygon_contact(
            rect(101.0, 101.0, 102.0, 102.0), ring
        )
        self.assertEqual(inside, ContactStatus.INTERFERENCE)
        self.assertEqual(outside, ContactStatus.CLEAR)

    def test_dense_tool_open_cavity_still_clear(self):
        from math import cos, sin, pi
        outer = tuple((100*cos(2*pi*i/256), 100*sin(2*pi*i/256))
                      for i in range(256))
        hole = tuple((10*cos(2*pi*i/256), 10*sin(2*pi*i/256))
                     for i in range(256))
        status, _ = classify_polygon_contact(rect(-1,-1,1,1), outer, (hole,))
        self.assertEqual(status, ContactStatus.CLEAR)

    def test_fast_winding_boundary_and_hole_consistency(self):
        # Regression for combined O(n) winding/boundary pass.
        from smbs_core.kinematics2d.contact_oracle_2d import _point_ring
        square = rect(-4, -3, 4, 3)
        reversed_square = tuple(reversed(square))
        for ring in (square, reversed_square):
            self.assertEqual(_point_ring((0, 0), ring, 0.02), 1)
            self.assertEqual(_point_ring((4, 2), ring, 0.02), 0)
            self.assertEqual(_point_ring((4.01, 2), ring, 0.02), 0)
            self.assertEqual(_point_ring((5, 0), ring, 0.02), -1)

    def test_tool_polygon_validation_is_reused_for_many_panels(self):
        from unittest.mock import patch
        from smbs_core.kinematics2d import contact_oracle_2d as oracle
        polygons = tuple(("P%02d" % i, rect(-1, 1+i, 1, 2+i))
                         for i in range(12))
        original = oracle._validate_ring
        with patch.object(oracle, "_validate_ring", wraps=original) as validation:
            report = check_machine_section(
                polygons, (rect(-3, -5, 3, -3), ()),
                (rect(-3, -10, 3, -8), ()),
            )
        self.assertEqual(report.status, ContactStatus.CLEAR)
        self.assertEqual(len(report.findings), 24)
        # 12 sheet rings per tool plus ONE ring for each tool, not 24.
        self.assertEqual(validation.call_count, 26)

    def test_invalid_tool_geometry_remains_unverified(self):
        report = check_machine_section(
            (("P01", rect(-1,0,1,1)), ("P02", rect(-1,2,1,3))),
            ((0,0), ()), (rect(-3,-5,3,-3), ()),
        )
        self.assertEqual(report.status, ContactStatus.UNVERIFIED)
        self.assertEqual([f.status for f in report.findings if f.tool == "PUNCH"],
                         [ContactStatus.UNVERIFIED, ContactStatus.UNVERIFIED])

    def test_report_checks_every_sheet_component_and_tool(self):
        report=check_machine_section(
            (("B01",rect(-1,2,1,3)),("P02",rect(0,-0.25,1,0.25))),
            (rect(-2,4,2,5),()),
            (rect(-2,-1,2,0),()),
        )
        self.assertEqual(report.status,ContactStatus.INTERFERENCE)
        self.assertEqual(len(report.findings),4)
        self.assertTrue(any(x.tool=="DIE" and x.sheet_component=="P02" and
                            x.status==ContactStatus.INTERFERENCE
                            for x in report.findings))

    def test_real_machine_section_adapter_checks_all_sheet_panels(self):
        class Tooling:
            aligned=True
            def punch_polygons_machine(self,arc_segments=24):
                return rect(-2,4,2,6),()
            def die_polygons_machine(self,arc_segments=24):
                return rect(-2,-2,2,0),()
        section={
            "fixed_polygon":rect(-2,1,0,2),
            "moving_polygon":rect(0,1,2,2),
            "zone_polygon":rect(-0.5,0,0.5,1),
            "other_panel_polygons":(("P03",rect(-2,5,-1,6)),),
            "other_zone_polygons":(),
        }
        report=check_sheet_tooling_preview(section,Tooling())
        self.assertEqual(report.status,ContactStatus.INTERFERENCE)
        self.assertTrue(any(x.sheet_component=="P03" and x.tool=="PUNCH"
                            and x.status==ContactStatus.INTERFERENCE
                            for x in report.findings))

    def test_unaligned_tooling_is_never_declared_clear(self):
        class Tooling:
            aligned=False
        section={"fixed_polygon":rect(0,0,1,1)}
        report=check_sheet_tooling_preview(section,Tooling())
        self.assertEqual(report.status,ContactStatus.UNVERIFIED)

    def test_missing_tool_is_unverified_not_pass(self):
        report=check_machine_section(
            (("B01",rect(-1,2,1,3)),),None,(rect(-2,-1,2,0),())
        )
        self.assertEqual(report.status,ContactStatus.UNVERIFIED)

    def test_invalid_polygons_rejected(self):
        with self.assertRaises(ContactGeometryError):
            classify_polygon_contact(((0,0),(1,0)),rect(-2,-2,2,2))
        with self.assertRaises(ContactGeometryError):
            classify_polygon_contact(rect(-1,-1,1,1),rect(-2,-2,2,2),
                                     tolerance_mm=0)
        report=check_machine_section(
            (("bad",((0,0),(1,0))),),(rect(-2,-2,2,2),()),None
        )
        self.assertEqual(report.status,ContactStatus.UNVERIFIED)


if __name__=="__main__":
    unittest.main()
