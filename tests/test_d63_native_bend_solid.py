"""D6.3 native one-zone geometry contract tests (pure math + optional FreeCAD)."""
import math
import unittest
from unittest.mock import patch

from smbs_core.kinematics2d.bend_zone import (
    bend_parameters, make_bend_zone,
)
from smbs_core.kinematics2d.panel_graph import PanelGraph, GraphEdge
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeBendSolidError, build_bend_zone_solid, zone_outline,
)


def fixture(source_angle=90.0):
    thickness=1.5
    reference_radius = 1.0 if source_angle > 0 else 2.5
    params = bend_parameters(source_angle,reference_radius,thickness,0.4)
    z=make_bend_zone("B01",((0.,0.),(100.,0.)),params)
    graph=PanelGraph(("P01","P02"),(
        GraphEdge("B01","P01","P02",params.fold_angle_deg,
                  ((0.,0.,0.),(100.,0.,0.)),z),
    ))
    return FoldState(graph,"P01")


def planar_area(points):
    return abs(sum(p[0]*q[1]-q[0]*p[1]
                   for p,q in zip(points,points[1:]+points[:1]))/2)


class ZoneOutlineTests(unittest.TestCase):
    def test_flat_volume_section_area(self):
        for signed in (90.,-90.,135.,-135.):
            state=fixture(signed)
            z=state.bends["B01"].bend_zone
            outline=zone_outline(z,0.)
            self.assertAlmostEqual(
                planar_area(outline),
                z.bend_allowance_mm*z.thickness_mm,places=6,
            )

    def test_all_angles_are_positive_area(self):
        for source in (90.,-90.,135.,-135.,45.):
            state=fixture(source)
            z=state.bends["B01"].bend_zone
            for fraction in (0.,0.25,0.5,0.75,1.):
                points=zone_outline(z,z.fold_angle_deg*fraction)
                self.assertGreater(planar_area(points),0.01)
                self.assertTrue(all(math.isfinite(x) and math.isfinite(y) for x,y in points))

    def test_no_adjacent_duplicates(self):
        z=fixture().bends["B01"].bend_zone
        for a in (0.,45.,90.):
            pts=zone_outline(z,a)
            self.assertTrue(all(math.dist(p,q)>1e-8 for p,q in zip(pts,pts[1:]+pts[:1])))

    def test_bad_sampling_fails(self):
        with self.assertRaises(NativeBendSolidError):
            zone_outline(fixture().bends["B01"].bend_zone,0,segments=4)

    def test_missing_zone_fails_closed(self):
        z=fixture()
        with self.assertRaises(NativeBendSolidError):
            build_bend_zone_solid(z,"NOT_A_BEND")

    def test_live_freecad_part_if_available(self):
        try:
            import FreeCAD
            import Part
        except ImportError:
            self.skipTest("native FreeCAD Part runtime is required")
        for source in (90.,-90.,135.,-135.):
            state=fixture(source)
            for fraction in (0.,0.25,0.5,0.75,1.):
                state.set_fraction("B01",fraction)
                result=build_bend_zone_solid(state,"B01")
                self.assertTrue(result.shape.isValid())
                self.assertTrue(result.shape.isClosed())
                self.assertEqual(len(result.shape.Solids),1)
                self.assertGreater(result.volume_mm3,0)
                self.assertAlmostEqual(
                    result.volume_mm3, result.section_area_mm2 * result.bend_line_length_mm,
                    places=4,
                )
                self.assertFalse(result.physical_certified)


if __name__=="__main__":
    unittest.main()
