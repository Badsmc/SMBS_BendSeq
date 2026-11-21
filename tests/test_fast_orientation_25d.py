"""Regressions for physical four-pose loading diagnostics.

Headless tests deliberately do NOT claim collision-free machine setup.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.fast_orientation_25d import (
    four_loading_poses, roll_about_machine_bend_axis, upper_punch_face_score,
)
from smbs_core.kinematics2d.fast_proxy_tool_screen import (
    ProxyFinding, diagnose_four_loading_poses,
)
from smbs_core.kinematics2d.workpiece_placement_3d import (
    PlacementTransform3D,
)


I = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))


class TestFourPoseLoading25D(TestCase):
    def test_exactly_four_distinct_candidates_and_two_eligible_yaws(self):
        poses = four_loading_poses()
        self.assertEqual(len(poses), 4)
        self.assertEqual(len({p.pose_id for p in poses}), 4)
        self.assertEqual(
            [(p.reverse_axis,p.roll_180) for p in poses],
            [(False,False),(True,False),(False,True),(True,True)],
        )
        self.assertEqual(
            sum(p.eligible_for_fast_ranking for p in poses), 2,
        )

    def test_longitudinal_roll_is_proper_rigid_3d_rotation(self):
        original=PlacementTransform3D(I,(1.,2.,3.))
        rolled=roll_about_machine_bend_axis(original,axis_z_mm=8.)
        # Rotation around the machine bend line fixes (Y=0, Z=8).
        self.assertEqual(
            tuple(round(x,6) for x in rolled.apply((4.,-2.,5.))),
            (5.,-0.,8.),
        )
        base=PlacementTransform3D(I,(0.,0.,0.))
        turned=roll_about_machine_bend_axis(base,axis_z_mm=8.)
        self.assertEqual(turned.apply((4.,0.,8.)),(4.,0.,8.))
        self.assertEqual(turned.apply((4.,3.,11.)),(4.,-3.,5.))
        self.assertEqual(
            turned.inverse().apply(turned.apply((5.,3.,11.))),
            (5.,3.,11.),
        )
        # 180 around X flips BOTH Y and Z: det=+1, not a 2D mirror.
        self.assertEqual(
            turned.rotation,
            ((1.,0.,0.),(0.,-1.,0.),(0.,0.,-1.)),
        )

    def test_roll_inverts_signed_punch_face(self):
        bend=SimpleNamespace(
            fixed_panel="P05",angle_deg=90.,
            bend_zone=SimpleNamespace(fold_angle_deg=90.),
        )
        state=SimpleNamespace(
            bends={"B01":bend},
            transforms={"P05":SimpleNamespace(rotation=I)},
        )
        p=PlacementTransform3D(I,(0.,0.,0.))
        roll=roll_about_machine_bend_axis(p,axis_z_mm=0.)
        self.assertGreater(upper_punch_face_score(state,"B01",p),0.)
        self.assertLess(upper_punch_face_score(state,"B01",roll),0.)

    def test_four_diagnostics_never_approve_rolled_clearance(self):
        seen=[]
        def screen(*args,**kwargs):
            seen.append((args[5],kwargs.get("roll_180")))
            return ProxyFinding(
                "INVALID_PUNCH_FACE" if kwargs["roll_180"] else "CLEAR_SAMPLED",
                samples=2,
            )
        with patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "screen_proxy_transition",side_effect=screen,
        ):
            records=diagnose_four_loading_poses(
                object(),{},(),0,0,object(),
            )
        self.assertEqual(len(records),4)
        self.assertEqual(
            [(x["reverse_axis"],x["roll_180"]) for x in records],
            [(False,False),(True,False),(False,True),(True,True)],
        )
        self.assertTrue(all(
            x["finding"]["status"]=="INVALID_PUNCH_FACE"
            and not x["eligible_for_fast_ranking"]
            for x in records if x["roll_180"]
        ))
        self.assertEqual(len(seen),4)


    def test_stage_b_failed_b01_template_is_diagnosed_even_if_beam_was_clear(self):
        from smbs_core.kinematics2d.fast_proxy_tool_screen import (
            diagnose_shortlist_obstructions_25d,
        )
        operations=tuple(
            SimpleNamespace(operation_id=b,bend_id=b)
            for b in ("B09","B10","B06","B05","B07","B01","B03","B08","B04","B02")
        )
        screen=SimpleNamespace(checks=[
            SimpleNamespace(
                failed_transition=(31,"B01",False),
                first_issue="B01:PUNCH:P11",
            ),
            SimpleNamespace(
                failed_transition=(24,"B01",False),
                first_issue="B01:PUNCH:P11",
            ),
            SimpleNamespace(
                failed_transition=(2,"B06",False),
                first_issue="B06:PUNCH:P02",
            ),
        ])
        seen=[]
        with patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen.prepare_proxy_tools",
            return_value=object(),
        ),patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "diagnose_four_loading_poses",
            side_effect=lambda state,polygons,steps,mask,index,tools,**kwargs: (
                seen.append((mask,steps[index].operation_id))
                or ({"pose_id":"YAW_0_ROLL_0",
                     "finding":{"status":"POTENTIAL_INTERFERENCE"}},)
            ),
        ):
            report=diagnose_shortlist_obstructions_25d(
                object(),{},operations,screen,object(),
                max_transitions=3,budget_seconds=10,
            )
        self.assertEqual(report["status"],"SAMPLED_DIAGNOSTIC_ONLY")
        self.assertEqual(
            seen,[(31,"B01"),(24,"B01"),(2,"B06")],
        )
        self.assertEqual(
            report["cases"][0]["preceding_operations"],
            ["B09","B10","B06","B05","B07"],
        )
        self.assertEqual(
            report["cases"][0]["stage_b_first_issue"],"B01:PUNCH:P11",
        )


if __name__=="__main__":
    import unittest
    unittest.main()
