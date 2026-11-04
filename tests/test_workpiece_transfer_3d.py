"""C1/C2: physical-point continuity and placement validation, no FreeCAD."""
import math
import unittest
from smbs_core.kinematics2d.bend_zone import bend_parameters, make_bend_zone
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D, PlacementTransform3D, _mat_det
from smbs_core.kinematics2d.workpiece_placement_validation_3d import (
    PlacementValidationError, validate_workpiece_placement, validate_rigid_3d,
)
from smbs_core.kinematics2d.workpiece_transfer_3d import build_bend_transfer, build_transfer_3d


class PlacementTransferTests(unittest.TestCase):
    def setUp(self):
        pa = bend_parameters(90.0,1.0,1.5,0.4)
        bz1 = make_bend_zone("B01",((0,50),(100,50)),pa)
        bz2 = make_bend_zone("B02",((50,50),(50,150)),pa)
        graph = PanelGraph(("P00","P01","P02"),(
            GraphEdge("B01","P00","P01",90,((0,50,0),(100,50,0)),bz1),
            GraphEdge("B02","P00","P02",90,((50,50,0),(50,150,0)),bz2),
        ))
        self.state = FoldState(graph,"P00")
        self.vertices = {
            "P00":((0,0),(100,0),(100,50),(0,50)),
            "P01":((0,52),(100,52),(100,100),(0,100)),
            "P02":((52,50),(100,50),(100,150),(52,150)),
        }

    def test_c1_validates_nonparallel_axes_without_mutating_fold_state(self):
        self.state.set_angle("B01",90)
        before=(dict(self.state.angles),dict(self.state.transforms))
        for bid in ("B01","B02"):
            p=WorkpiecePlacement3D.compute_placement(self.state,bid)
            result=validate_workpiece_placement(self.state,bid,p,self.vertices)
            self.assertLess(result.max_axis_error_mm,1e-8)
            self.assertEqual(result.vertex_count,12)
            self.assertFalse(result.collision_validated)
            self.assertFalse(result.support_validated)
        self.assertEqual(before,(self.state.angles,self.state.transforms))

    def test_c1_rejects_translation_and_mirror_and_missing_panel(self):
        p=WorkpiecePlacement3D.compute_placement(self.state,"B01")
        wrong=PlacementTransform3D(p.rotation,(p.translation[0],p.translation[1]+3,p.translation[2]))
        with self.assertRaises(PlacementValidationError):
            validate_workpiece_placement(self.state,"B01",wrong,self.vertices)
        mirror=PlacementTransform3D(((-1,0,0),(0,1,0),(0,0,1)),(0,0,0))
        with self.assertRaises(PlacementValidationError):
            validate_rigid_3d(mirror)
        with self.assertRaises(PlacementValidationError):
            validate_workpiece_placement(self.state,"B01",p,{"P00":self.vertices["P00"]})

    def test_c2_not_allowed_before_first_bend_completed(self):
        with self.assertRaises(PlacementValidationError):
            build_bend_transfer(self.state,"B01","B02",
                                max_linear_mm_s=100,max_angular_deg_s=90)

    def test_c2_exact_endpoints_and_true_physical_continuity(self):
        self.state.set_angle("B01",90)
        before=(dict(self.state.angles),dict(self.state.transforms))
        motion=build_bend_transfer(self.state,"B01","B02",
                                   max_linear_mm_s=120,max_angular_deg_s=60)
        self.assertGreater(motion.duration_s,0)
        self.assertGreater(motion.angular_displacement_deg,1)
        self.assertEqual(motion.pose_at(0),motion.start)
        self.assertEqual(motion.pose_at(motion.duration_s),motion.end)
        self.assertFalse(motion.collision_validated)

        # Same material point P00[0] is sampled in each machine pose.
        physical_point=self.state.transforms["P00"].point((0,0,0))
        prev=None
        initial=motion.start.apply(physical_point)
        self.assertGreater(math.dist(initial,motion.end.apply(physical_point)),1)
        steps=200
        for i in range(steps+1):
            time=motion.duration_s*i/steps
            pose=motion.pose_at(time)
            validate_rigid_3d(pose)
            machine_pt=pose.apply(physical_point)
            if prev is not None:
                self.assertLess(math.dist(machine_pt,prev),10.0)
            prev=machine_pt
            # Measure actual rigid-panel edge; it cannot stretch in transit.
            edge0=pose.apply(self.state.transforms["P00"].point((0,0,0)))
            edge1=pose.apply(self.state.transforms["P00"].point((100,0,0)))
            self.assertAlmostEqual(math.dist(edge0,edge1),100,places=7)
        self.assertEqual(before,(self.state.angles,self.state.transforms))

    def test_c2_rate_limits_and_invalid_parameters(self):
        self.state.set_angle("B01",90)
        motion=build_bend_transfer(self.state,"B01","B02",
                                   max_linear_mm_s=20,max_angular_deg_s=15)
        self.assertLessEqual(motion.translation_distance_mm/motion.duration_s,20+1e-8)
        self.assertLessEqual(motion.angular_displacement_deg/motion.duration_s,15+1e-8)
        with self.assertRaises(ValueError):
            build_transfer_3d(motion.start,motion.end,
                              max_linear_mm_s=0,max_angular_deg_s=15)
        with self.assertRaises(ValueError):
            motion.pose_at(float("nan"))


if __name__=="__main__":
    unittest.main()
