"""D5.1 regression for sheet material side and collapsed bend-zone sections.

The synthetic fold state is deliberately minimal: the production cut-section
adapter is exercised, without importing FreeCAD or inventing machine tooling.
"""
import math
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.machine_section_3d import (
    _compact_closed_section_polygon,
    compute_machine_cutting_plane_section,
)


class ShiftedPanel:
    def __init__(self, invert_z=False):
        self.invert_z = invert_z

    def point(self, xyz):
        x, y, z = xyz
        return (float(x), float(y), -1.5 + (-z if self.invert_z else z))


class Placement:
    def apply(self, xyz):
        return xyz


class Fold:
    def __init__(self, sign):
        zone = SimpleNamespace(
            thickness_mm=1.5,
            inner_radius_mm=0.8,
            fold_angle_deg=90.0 * sign,
            axis_xy=((-1.0, 0.0), (1.0, 0.0)),
            left_xy=(0.0, 1.0),
        )
        self.bends = {"B01": SimpleNamespace(
            bend_zone=zone,
            fixed_panel="P1",
            moving_panel="P2",
            moving_left=True,
            left_xy=(0.0, 1.0),
            angle_deg=90.0 * sign,
        )}
        self.graph = SimpleNamespace(panels={"P1": object(), "P2": object()})
        self.transforms = {
            "P1": ShiftedPanel(invert_z=(sign < 0)),
            "P2": ShiftedPanel(invert_z=(sign < 0)),
        }

    def zone_section(self, bend_id, segments):
        # Production bug fixture: many duplicates collapse at zero bend.
        return {
            "inner": ((-1.0, 0.0), (0.0, 0.0), (0.0, 0.0), (1.0, 0.0)),
            "outer": ((-1.0, -1.5), (0.0, -1.5),
                      (0.0, -1.5), (1.0, -1.5)),
            "neutral": ((-1.0, -0.75), (1.0, -0.75)),
        }


class MaterialSideRegression(unittest.TestCase):
    VERTS = {
        "P1": ((-1, -10), (1, -10), (1, -1), (-1, -1)),
        "P2": ((-1, 1), (1, 1), (1, 10), (-1, 10)),
    }

    def _section(self, sign):
        return compute_machine_cutting_plane_section(
            Fold(sign), self.VERTS, Placement(), active_bend_id="B01",
            segments=32,
        )

    def test_positive_flat_sheet_bottom_at_die_support(self):
        sec = self._section(1)
        poly = sec["fixed_polygon"]
        self.assertEqual(len(poly), 4)
        self.assertAlmostEqual(min(z for s, z in poly), -1.5)
        self.assertAlmostEqual(max(z for s, z in poly), 0.0)
        self.assertAlmostEqual(min(z for s, z in sec["zone_polygon"]), -1.5)
        self.assertAlmostEqual(max(z for s, z in sec["zone_polygon"]), 0.0)

    def test_negative_flat_sheet_same_support_plane(self):
        sec = self._section(-1)
        poly = sec["fixed_polygon"]
        self.assertEqual(len(poly), 4)
        self.assertAlmostEqual(min(z for s, z in poly), -1.5)
        self.assertAlmostEqual(max(z for s, z in poly), 0.0)
        self.assertAlmostEqual(min(z for s, z in sec["zone_polygon"]), -1.5)
        self.assertAlmostEqual(max(z for s, z in sec["zone_polygon"]), 0.0)

    def test_compaction_retains_thickness(self):
        for sign in (1, -1):
            sec = self._section(sign)
            polygon = sec["zone_polygon"]
            self.assertEqual(len(polygon), 4)
            self.assertEqual(len(set(polygon)), 4)
            self.assertGreater(max(z for s, z in polygon)-min(z for s, z in polygon), 1.49)

    def test_repeated_adjacent_vertices_are_removed(self):
        points = ((0, 0), (0, 0), (1, 0), (1, 1),
                  (0, 1), (0, 0), (0, 0))
        self.assertEqual(len(_compact_closed_section_polygon(points)), 4)

    def test_no_mutation_of_fold_state(self):
        fold = Fold(1)
        angle = fold.bends["B01"].angle_deg
        compute_machine_cutting_plane_section(fold, self.VERTS, Placement(), active_bend_id="B01")
        self.assertEqual(fold.bends["B01"].angle_deg, angle)


if __name__ == "__main__":
    unittest.main()
