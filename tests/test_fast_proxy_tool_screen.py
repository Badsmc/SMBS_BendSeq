"""FAST search-only selected-tool silhouette scoring, independent of FreeCAD."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.fast_proxy_tool_screen import (
    ProxyFinding,ProxyTool,ProxyTools,prepare_proxy_tools,
    screen_proxy_transition,simplify_tool_ring,
)
from smbs_core.kinematics2d.staged_bends import BendOperation


class TestProxyToolScreen(TestCase):
    def setup_scene(self):
        panel_ids=("A","B","OTHER")
        panel_vertices={
            "A":((0.,0.),(1.,0.),(1.,1.)),
            "B":((10.,0.),(11.,0.),(11.,1.)),
            "OTHER":((20.,0.),(21.,0.),(21.,1.)),
        }
        graph=SimpleNamespace(panels=panel_ids)
        bend=SimpleNamespace(
            bend_zone=SimpleNamespace(thickness_mm=1.5,outer_radius_mm=2.0),
            fixed_panel="A",moving_panel="B",
        )
        state=SimpleNamespace(
            graph=graph,bends={"B01":bend},
            fixed_panel="A",angles={"B01":0.0},
            transforms={"A":"a","B":"b","OTHER":"o"},
        )
        state._compute_transforms=lambda angles: dict(state.transforms)
        steps=(BendOperation("B01","B01",90.),)
        tools=ProxyTools(
            ProxyTool("PUNCH","P",((-2.,-3.),(2.,-3.),(2.,0.),(-2.,0.)),()),
            ProxyTool("DIE","D",((-3.,-4.),(3.,-4.),(3.,0.),(-3.,0.)),()),
            0.35,0.5,
        )
        return state,panel_vertices,steps,tools

    def test_selected_tool_profile_uses_local_geometry_not_fabricated_proxy(self):
        class Tool:
            validation_status=SimpleNamespace(value="GEOMETRY_VERIFIED")
            def __init__(self,name,points):
                self.tool_id=name
                self._points=points
            def local_polygons(self,arc_segments=20):
                return self._points,()
        contour=((-3.,-20.),(-1.,-8.),(0.,0.),(1.,-8.),(3.,-20.),
                 (3.,-40.),(-3.,-40.))
        result=prepare_proxy_tools(SimpleNamespace(
            punch=Tool("P-A",contour),die=Tool("D-B",tuple(
                (x,y-45) for x,y in contour
            )),
        ))
        self.assertIsNotNone(result)
        self.assertEqual(result.punch.tool_id,"P-A")
        self.assertEqual(result.die.tool_id,"D-B")
        self.assertIn((0.,0.),result.punch.outer)
        self.assertGreaterEqual(len(result.punch.outer),3)

    def test_missing_or_unverified_profile_does_not_invent_geometry(self):
        self.assertIsNone(prepare_proxy_tools(None))
        self.assertIsNone(prepare_proxy_tools(SimpleNamespace(
            punch=None,die=None,
        )))
        tool=SimpleNamespace(validation_status=SimpleNamespace(
            value="IMPORTED",
        ))
        self.assertIsNone(prepare_proxy_tools(SimpleNamespace(
            punch=tool,die=tool,
        )))

    def test_simplification_removes_unnecessary_segment_points(self):
        many=((-5.,-5.),(0.,-5.),(5.,-5.),(5.,0.),
              (5.,5.),(0.,5.),(-5.,5.),(-5.,0.))
        reduced=simplify_tool_ring(many,0.35)
        self.assertLessEqual(len(reduced),len(many))
        self.assertEqual(len(reduced),4)

    def test_reverse_axis_changes_penalty_without_mutating_sheet(self):
        state,polygons,operations,tools=self.setup_scene()
        def fake_section(poly,transform,placement,thickness):
            if poly[0][0]!=20.:
                return ((30.,0.),(31.,0.),(31.,1.),(30.,1.))
            if placement.reverse:
                return ((30.,4.),(31.,4.),(31.,5.),(30.,5.))
            return ((-1.,4.),(1.,4.),(1.,5.),(-1.,5.))
        with patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "WorkpiecePlacement3D.compute_placement",
            side_effect=lambda view,bid,**kwargs: SimpleNamespace(
                reverse=kwargs["reverse_axis"],
            ),
        ),patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen._section_panel",
            side_effect=fake_section,
        ):
            bad=screen_proxy_transition(
                state,polygons,operations,0,0,False,tools,
            )
            good=screen_proxy_transition(
                state,polygons,operations,0,0,True,tools,
            )
        self.assertEqual(bad.status,"POTENTIAL_INTERFERENCE")
        self.assertEqual(bad.tool,"PUNCH")
        self.assertEqual(bad.component,"OTHER")
        self.assertGreater(bad.penalty,0)
        self.assertEqual(good.status,"CLEAR_SAMPLED")
        self.assertEqual(good.penalty,0)
        self.assertEqual(state.angles,{"B01":0.0})

    def test_active_forming_contact_not_penalized_at_mid_bend(self):
        state,polygons,operations,tools=self.setup_scene()
        def fake_section(poly,transform,placement,thickness):
            if poly[0][0]==20.:
                return ((30.,1.),(31.,1.),(31.,2.),(30.,2.))
            if placement.fraction==0.0:
                # Parked punch clear of active panel at Z>=3.5.
                return ((-1.,1.),(1.,1.),(1.,2.),(-1.,2.))
            # A true forming section intersects the tip: this is intended.
            return ((-1.,0.),(1.,0.),(1.,1.),(-1.,1.))
        with patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "WorkpiecePlacement3D.compute_placement",
            side_effect=lambda view,bid,**kwargs: SimpleNamespace(
                fraction=view.angles["B01"]/90.,
            ),
        ),patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen._section_panel",
            side_effect=fake_section,
        ):
            result=screen_proxy_transition(
                state,polygons,operations,0,0,False,tools,
            )
        self.assertEqual(result.status,"CLEAR_SAMPLED")
        self.assertEqual(result.penalty,0)

    def test_real_beam_search_favors_unblocked_orientation(self):
        # Run the actual Beam Search with a simulated cheap tool-overlap
        # oracle. The same 0/180 mask must be evaluated separately and
        # its proxy score must affect ranking *inside* the beam.
        from smbs_core.kinematics2d.fast_bfm_beam_search import (
            search_fast_candidates,
        )
        from smbs_core.kinematics2d.panel_graph import GraphEdge,PanelGraph
        from smbs_core.kinematics2d.panel_kinematics import FoldState
        edge=GraphEdge(
            "B01","P0","P1",90.,
            ((0.,0.,0.),(50.,0.,0.)),
        )
        sheet=FoldState(PanelGraph(("P0","P1"),(edge,)),"P0")
        sheet.bends={
            bid:replace(bend,bend_zone=object())
            for bid,bend in sheet.bends.items()
        }
        poly={
            "P0":((0.,-20.),(50.,-20.),(50.,-1.)),
            "P1":((0.,1.),(50.,1.),(50.,20.)),
        }
        calls=[]
        def fake_screen(state,vertices,steps,mask,index,reverse,tools,**kwargs):
            calls.append((mask,index,reverse))
            if not reverse:
                return ProxyFinding(
                    "POTENTIAL_INTERFERENCE",1500.,
                    "PUNCH","P1","APPROACH",
                    "proper_material_boundary_crossing",1,
                )
            return ProxyFinding("CLEAR_SAMPLED",samples=2)
        with patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "prepare_proxy_tools",return_value=object(),
        ),patch(
            "smbs_core.kinematics2d.fast_proxy_tool_screen."
            "screen_proxy_transition",side_effect=fake_screen,
        ):
            report=search_fast_candidates(
                sheet,panel_vertices_xy=poly,
                tooling_setup=object(),
                screen_tool_center_axis=False,
                beam_width=6,
            )
        self.assertTrue(report.candidates)
        self.assertTrue(report.proxy_tool_available)
        self.assertGreater(report.proxy_tool_penalized,0)
        self.assertEqual({reverse for _mask,_i,reverse in calls},
                         {False,True})
        self.assertTrue(report.candidates[0].orientations()["B01"])
        self.assertEqual(report.candidates[0].proxy_tool_penalty,0.)
        self.assertGreater(len(report.to_dict()["proxy_tool_findings"]),0)

    def test_time_budget_is_unknown_not_collision(self):
        state,polygons,operations,tools=self.setup_scene()
        result=screen_proxy_transition(
            state,polygons,operations,0,0,False,tools,
            deadline=0.,clock=lambda:10.,
        )
        self.assertEqual(result.status,"TIME_BUDGET")
        self.assertEqual(result.penalty,0)

    def test_staged_is_not_falsely_screened_as_single_bend(self):
        state,polygons,_,tools=self.setup_scene()
        steps=(BendOperation("PRE-B01","B01",30.,"PREBEND"),)
        result=screen_proxy_transition(
            state,polygons,steps,0,0,False,tools,
        )
        self.assertEqual(result.status,"UNKNOWN")
        self.assertEqual(result.penalty,0)
