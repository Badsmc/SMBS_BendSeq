"""D5.1 regression: verified tool sections, collisions, fail-closed states.

Run with: python -m unittest tests.test_d5_contact_validation
"""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.d5_contact_validation import (
    D51Status, check_d51_machine_section,
)


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


class VerifiedTooling:
    aligned = True
    aligned_bend_id = "B01"
    punch = SimpleNamespace(validation_status=SimpleNamespace(value="GEOMETRY_VERIFIED"))
    die = SimpleNamespace(validation_status=SimpleNamespace(value="GEOMETRY_VERIFIED"))

    def __init__(self, punch=None, die=None):
        self.punch_shape = punch if punch is not None else (rect(-2, 3, 2, 5), ())
        self.die_shape = die if die is not None else (rect(-3, -3, 3, 0), ())

    def punch_polygons_machine(self, arc_segments=32):
        return self.punch_shape

    def die_polygons_machine(self, arc_segments=32):
        return self.die_shape


def scene(**changes):
    result = {
        "active_bend_id": "B01",
        "cutting_plane_xm": 0.0,
        "fixed_polygon": rect(-1, 1, 0, 2),
        "moving_polygon": rect(0, 1, 1, 2),
        "zone_polygon": (),
        "other_panel_polygons": [],
        "other_zone_polygons": [],
    }
    result.update(changes)
    return result


class D51SectionChecks(unittest.TestCase):
    def test_clear(self):
        self.assertEqual(check_d51_machine_section(
            scene(), VerifiedTooling(), bend_id="B01", phase="APPROACH"
        ).status, D51Status.CLEAR)

    def test_die_boundary_contact(self):
        report = check_d51_machine_section(
            scene(fixed_polygon=rect(-1, 0, 0, 1), moving_polygon=()),
            VerifiedTooling(), bend_id="B01", phase="CONTACT")
        self.assertEqual(report.status, D51Status.CONTACT)

    def test_punch_material_penetration(self):
        report = check_d51_machine_section(
            scene(other_panel_polygons=[("P03", rect(-1, 3.5, 1, 4.5))]),
            VerifiedTooling(), bend_id="B01", phase="BENDING")
        self.assertEqual(report.status, D51Status.COLLISION)
        self.assertTrue(any(f.tool == "PUNCH" and f.component == "P03"
                            and f.status == D51Status.COLLISION for f in report.findings))

    def test_die_material_penetration(self):
        report = check_d51_machine_section(
            scene(fixed_polygon=rect(-1, -0.4, 0, 0.6)),
            VerifiedTooling(), bend_id="B01", phase="BENDING")
        self.assertEqual(report.status, D51Status.COLLISION)

    def test_hole_is_empty(self):
        tooling = VerifiedTooling(die=(rect(-5, -5, 5, 5),
                                       (rect(-3, -3, 3, 3),)))
        self.assertEqual(check_d51_machine_section(
            scene(), tooling, bend_id="B01", phase="BENDING"
        ).status, D51Status.CLEAR)

    def test_unaligned_does_not_pass(self):
        tooling = VerifiedTooling()
        tooling.aligned = False
        self.assertEqual(check_d51_machine_section(
            scene(), tooling, bend_id="B01", phase="BENDING"
        ).status, D51Status.UNVERIFIED)

    def test_missing_verified_datum_does_not_pass(self):
        tooling = VerifiedTooling()
        tooling.die = SimpleNamespace(validation_status=SimpleNamespace(value="IMPORTED"))
        self.assertEqual(check_d51_machine_section(
            scene(), tooling, bend_id="B01", phase="BENDING"
        ).status, D51Status.UNVERIFIED)

    def test_empty_section_does_not_pass(self):
        self.assertEqual(check_d51_machine_section(
            scene(fixed_polygon=(), moving_polygon=()), VerifiedTooling(),
            bend_id="B01", phase="BENDING"
        ).status, D51Status.UNVERIFIED)

    def test_wrong_machine_plane_does_not_pass(self):
        self.assertEqual(check_d51_machine_section(
            scene(cutting_plane_xm=10), VerifiedTooling(),
            bend_id="B01", phase="BENDING"
        ).status, D51Status.UNVERIFIED)

    def test_wrong_bend_does_not_pass(self):
        self.assertEqual(check_d51_machine_section(
            scene(), VerifiedTooling(), bend_id="B02", phase="BENDING"
        ).status, D51Status.UNVERIFIED)

    def test_invalid_polygon_does_not_pass(self):
        report = check_d51_machine_section(
            scene(fixed_polygon=((0, 0), (1, 1)), moving_polygon=()),
            VerifiedTooling(), bend_id="B01", phase="BENDING")
        self.assertEqual(report.status, D51Status.UNVERIFIED)

    def test_negative_direction_is_not_special_cased(self):
        for bend in ("B05", "B07"):
            tooling = VerifiedTooling()
            tooling.aligned_bend_id = bend
            report = check_d51_machine_section(
                scene(active_bend_id=bend,
                      fixed_polygon=rect(-1, -1, 0, 1),
                      moving_polygon=rect(0, -1, 1, 1)),
                tooling, bend_id=bend, phase="BENDING")
            self.assertEqual(report.status, D51Status.COLLISION)

    def test_read_only(self):
        section = scene()
        before = repr(section)
        tooling = VerifiedTooling()
        check_d51_machine_section(section, tooling, bend_id="B01", phase="BENDING")
        self.assertEqual(repr(section), before)

    def test_trace_schema_is_explicitly_preview_only(self):
        report = check_d51_machine_section(
            scene(), VerifiedTooling(), bend_id="B01", phase="BENDING")
        payload = report.as_trace()
        self.assertFalse(payload["collision_validated_3d"])
        self.assertEqual(payload["mode"], "2D_SECTION_PREVIEW_NOT_3D_CERTIFIED")


if __name__ == "__main__":
    unittest.main()
