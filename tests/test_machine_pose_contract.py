"""Headless regression for one physical loading frame across FAST, Stage B and Coin3D.

The common pose is an isometry; real continuous collision clearance is NOT
claimed by this test. The sheet angle, tool stroke, frame, and rear reference
must still be identical when comparing tools and the rendered machine.
"""
from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.bend_zone import BendParameters, make_bend_zone
from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics
from smbs_core.kinematics2d.fast_proxy_tool_screen import (
    ProxyTool, ProxyTools, _sampled_ram_stroke,
)
from smbs_core.kinematics2d.machine_pose_contract import (
    bend_support_plane_zm, compute_machine_pose, compute_machine_pose_frame,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState


def sample_bend():
    axis=((0.0,0.0,0.0),(50.0,0.0,0.0))
    zone=make_bend_zone(
        "B01",((0.0,0.0),(50.0,0.0)),
        BendParameters(90.0,1.5,0.3,1.0,1.45,2.5,2.278),
    )
    graph=PanelGraph(
        ("P0","P1"),(GraphEdge("B01","P1","P0",90.0,axis,zone),),
    )
    fold=FoldState(graph,"P0")
    polygons={
        "P0":((0.,-32.),(50.,-32.),(50.,-1.2),(0.,-1.2)),
        "P1":((0.,1.2),(50.,1.2),(50.,45.),(0.,45.)),
    }
    return fold,polygons


class TestUnifiedMachinePoseContract(TestCase):
    def test_same_bending_inputs_produce_same_three_dimensional_frame(self):
        fold,polygons=sample_bend()
        before=dict(fold.angles)
        fold.set_angle("B01",45.0)
        stroke=2.25
        height=bend_support_plane_zm(2.5,stroke)
        variants=[]
        for _consumer in ("FAST","STAGE_B","COIN3D"):
            frame=compute_machine_pose_frame(
                fold,"B01",
                phase="BENDING",
                support_plane_zm=height,
                reverse_axis=True,
                rear_panel_vertices_xy=polygons,
            )
            variants.append(frame.diagnostics())
        self.assertEqual(variants[0],variants[1])
        self.assertEqual(variants[1],variants[2])
        self.assertEqual(variants[0]["signed_angle_deg"],45.)
        self.assertEqual(variants[0]["support_plane_zm"],0.25)
        self.assertTrue(variants[0]["reverse_axis"])
        self.assertTrue(variants[0]["rear_frame_applied"])
        self.assertEqual(fold.angles["B01"],45.)
        self.assertEqual(before["B01"],0.)

    def test_phase_contract_changes_loading_bisector_not_sheet_geometry(self):
        fold,polygons=sample_bend()
        fold.set_angle("B01",40.)
        initial=dict(fold.angles)
        for phase,expected in (
            ("LOAD_TO_GAUGE",False),("APPROACH",False),("CONTACT",False),
            ("SETUP",True),("BENDING",True),("BOTTOM",True),
            ("RETRACT",True),("BEND_ONLY",True),
            ("NATIVE_SAMPLE",True),("TRANSFER_TARGET",True),
        ):
            with self.subTest(phase=phase):
                frame=compute_machine_pose_frame(
                    fold,"B01",phase=phase,support_plane_zm=0.5,
                    rear_panel_vertices_xy=polygons,
                )
                self.assertEqual(frame.align_bisector,expected)
                self.assertEqual(frame.phase,phase)
                self.assertEqual(frame.signed_angle_deg,40.)
                self.assertEqual(
                    frame.placement,
                    compute_machine_pose(
                        fold,"B01",phase=phase,support_plane_zm=0.5,
                        rear_panel_vertices_xy=polygons,
                    ),
                )
        self.assertEqual(fold.angles,initial)

    def test_reverse_axis_is_a_proper_rotation_not_mirrored_geometry(self):
        fold,polygons=sample_bend()
        fold.set_angle("B01",60.)
        p0=compute_machine_pose(
            fold,"B01",phase="BENDING",support_plane_zm=0.4,
            rear_panel_vertices_xy=polygons,
        )
        p180=compute_machine_pose(
            fold,"B01",phase="BENDING",support_plane_zm=0.4,
            reverse_axis=True,rear_panel_vertices_xy=polygons,
        )
        world=(10.,25.,5.)
        a=p0.apply(world)
        b=p180.apply(world)
        self.assertAlmostEqual(b[0],-a[0],places=6)
        self.assertAlmostEqual(b[1],-a[1],places=6)
        self.assertAlmostEqual(b[2],a[2],places=6)

    def test_support_height_matches_air_bending_and_retract_lift(self):
        outer=2.5
        tools=ProxyTools(
            ProxyTool("PUNCH","P",((0.,0.),(1.,0.),(0.,1.)),()),
            ProxyTool("DIE","D",((0.,0.),(1.,0.),(0.,1.)),()),
            0.35,0.5,12.,1.,
        )
        coupled=CoupledAirBendingKinematics(
            die_opening_v_mm=12.,die_shoulder_radius_mm=1.,
            sheet_thickness_mm=1.5,sheet_inner_radius_mm=1.,
        )
        for angle in (0.,22.5,45.,75.,90.):
            with self.subTest(angle=angle):
                proxy_stroke=_sampled_ram_stroke(angle,outer,tools)
                coupled_stroke=coupled.stroke_for_angle(angle)
                self.assertAlmostEqual(proxy_stroke,coupled_stroke,places=9)
                self.assertAlmostEqual(
                    bend_support_plane_zm(outer,proxy_stroke),
                    bend_support_plane_zm(outer,coupled_stroke),
                    places=9,
                )
                self.assertAlmostEqual(
                    bend_support_plane_zm(outer,coupled_stroke,retract_lift_mm=2.),
                    outer-coupled_stroke+2.,
                    places=9,
                )

    def test_invalid_machine_pose_inputs_fail_closed(self):
        fold,poly=sample_bend()
        for phase in ("NONSENSE","TRANSFER",""):
            with self.assertRaises(ValueError):
                compute_machine_pose(
                    fold,"B01",phase=phase,support_plane_zm=1.,
                    rear_panel_vertices_xy=poly,
                )
        for height in (float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                compute_machine_pose(
                    fold,"B01",phase="BENDING",support_plane_zm=height,
                    rear_panel_vertices_xy=poly,
                )
        for height in (float("nan"),float("inf"),-1.):
            with self.assertRaises(ValueError):
                bend_support_plane_zm(2.5,height)

    def test_all_four_render_and_screen_paths_call_shared_pose(self):
        repo=Path(__file__).resolve().parents[1]
        implementations={
            "smbs_core/kinematics2d/fast_proxy_tool_screen.py":"placement=compute_machine_pose(",
            "smbs_core/kinematics2d/preliminary_screening.py":"pose = compute_machine_pose(",
            "smbs_core/kinematics2d/press_cycle.py":"pose = compute_machine_pose(",
            "freecad_smbs/commands/pressbrake_2d.py":"return compute_machine_pose(",
        }
        for rel,call in implementations.items():
            with self.subTest(path=rel):
                source=(repo / rel).read_text(encoding="utf-8")
                self.assertIn(call,source)
        self.assertIn(
            "end_pose = compute_machine_pose(",
            (repo/"smbs_core/kinematics2d/press_cycle.py").read_text(
                encoding="utf-8",
            ),
        )


if __name__=="__main__":
    import unittest
    unittest.main()
