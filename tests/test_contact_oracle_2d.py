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
    def test_collision_sampling_reduces_zero_and_shallow_arc_segments(self):
        from types import SimpleNamespace
        from smbs_core.kinematics2d.machine_section_3d import (
            _collision_aware_zone_segments
        )
        zone = SimpleNamespace(inner_radius_mm=1.0, outer_radius_mm=2.5)
        # Existing 2D rendering still uses the caller's 32 segments.
        self.assertEqual(
            _collision_aware_zone_segments(zone, 7.5, 32, None), 32
        )
        # At small angles 32 segments generate contact-predicate
        # sub-tolerance edges; use just enough to retain the arc shape.
        self.assertEqual(
            _collision_aware_zone_segments(zone, 0.0, 32, 0.02), 1
        )
        self.assertEqual(
            _collision_aware_zone_segments(zone, 7.5, 32, 0.02), 3
        )
        self.assertEqual(
            _collision_aware_zone_segments(zone, 90.0, 32, 0.02), 32
        )
        with self.assertRaises(ValueError):
            _collision_aware_zone_segments(zone, 30.0, 32, 0.0)

    def test_flat_bend_zone_duplicate_vertices_preserve_clear(self):
        # BendZone.section_profile at zero angle repeats its arc
        # vertices 32 times. That is valid flat geometry, not an
        # ill-defined zero-area sheet polygon.
        flat_zone = (
            (-1.0, 0.0), (-1.0, 0.0),
            *((-1.0, 0.0) for _ in range(32)),
            (1.0, 0.0), (1.0, -1.5),
            *((1.0, -1.5) for _ in range(32)),
            (-1.0, -1.5), (-1.0, 0.0),
        )
        status, reason = classify_polygon_contact(
            flat_zone, rect(-3.0, 2.0, 3.0, 5.0)
        )
        self.assertEqual(status, ContactStatus.CLEAR)
        self.assertEqual(reason, "separated_bounding_boxes")

    def test_flat_bend_zone_duplicate_vertices_preserve_interference(self):
        flat_zone = (
            (-1.0, 0.0), (-1.0, 0.0), (-1.0, 0.0),
            (1.0, 0.0), (1.0, -1.5), (1.0, -1.5),
            (-1.0, -1.5), (-1.0, 0.0),
        )
        status, reason = classify_polygon_contact(
            flat_zone, rect(-0.5, -1.0, 0.5, -0.5)
        )
        self.assertEqual(status, ContactStatus.INTERFERENCE)

    def test_short_nonzero_edge_is_not_silently_removed(self):
        # A physical feature shorter than tolerance must remain
        # uncertain; deduplicating it as if it were a repeated vertex
        # could hide a real collision.
        short_edge = (
            (0.0, 0.0), (0.001, 0.0), (1.0, 0.0),
            (1.0, 1.0), (0.0, 1.0),
        )
        with self.assertRaisesRegex(ContactGeometryError, "Degenerate polygon edge"):
            classify_polygon_contact(short_edge, rect(-0.5, -0.5, 0.5, 0.5))

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
