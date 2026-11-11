"""Regression tests for the staged 2D collision cascade.

These tests cover only planar geometry screening, not 3D machine approval.
"""
import unittest

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactStatus, check_machine_section, check_sheet_tooling_preview,
)


def rectangle(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


class FakeTooling:
    aligned = True

    def __init__(self, punch, die):
        self.punch, self.die = punch, die
        self.punch_calls = 0
        self.die_calls = 0

    def punch_polygons_machine(self, arc_segments=24):
        self.punch_calls += 1
        return self.punch

    def die_polygons_machine(self, arc_segments=24):
        self.die_calls += 1
        return self.die


class CascadeContactTests(unittest.TestCase):
    section = {"fixed_polygon": rectangle(-1, -1, 1, 1)}

    def test_punch_collision_skips_die_geometry(self):
        tooling = FakeTooling(
            (rectangle(-2, -2, 2, 2), ()),
            (rectangle(10, 10, 12, 12), ()),
        )
        result = check_sheet_tooling_preview(self.section, tooling, cascade=True)
        self.assertEqual(result.status, ContactStatus.INTERFERENCE)
        self.assertEqual(tooling.punch_calls, 1)
        self.assertEqual(tooling.die_calls, 0)
        self.assertTrue(all(f.tool == "PUNCH" for f in result.findings))

    def test_clear_punch_still_checks_die(self):
        tooling = FakeTooling(
            (rectangle(10, 10, 12, 12), ()),
            (rectangle(-2, -2, 2, 2), ()),
        )
        result = check_sheet_tooling_preview(self.section, tooling, cascade=True)
        self.assertEqual(result.status, ContactStatus.INTERFERENCE)
        self.assertEqual(tooling.die_calls, 1)
        self.assertTrue(any(f.tool == "DIE" and
                            f.status is ContactStatus.INTERFERENCE
                            for f in result.findings))

    def test_clear_sections_preserve_both_tool_results(self):
        punch = (rectangle(10, 10, 12, 12), ())
        die = (rectangle(-12, -12, -10, -10), ())
        tooling = FakeTooling(punch, die)
        old = check_sheet_tooling_preview(self.section, tooling)
        new = check_sheet_tooling_preview(self.section, tooling, cascade=True)
        self.assertEqual(old.status, new.status)
        self.assertEqual(old.findings, new.findings)

    def test_unknown_punch_must_not_be_pruned_before_die(self):
        tooling = FakeTooling(None, (rectangle(10, 10, 12, 12), ()))
        result = check_sheet_tooling_preview(self.section, tooling, cascade=True)
        self.assertEqual(result.status, ContactStatus.UNVERIFIED)
        self.assertEqual(tooling.die_calls, 1)

    def test_selected_tool_only_does_not_invent_missing_tool(self):
        report = check_machine_section(
            [("panel", rectangle(-1, -1, 1, 1))],
            (rectangle(10, 10, 12, 12), ()), None,
            selected_tools=("PUNCH",),
        )
        self.assertEqual(report.status, ContactStatus.CLEAR)
        self.assertEqual(len(report.findings), 1)


if __name__ == "__main__":
    unittest.main()
