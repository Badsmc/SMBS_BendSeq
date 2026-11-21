"""FAST search-only selected-tool silhouette scoring, independent of FreeCAD."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.fast_proxy_tool_screen import (
    ProxyTool,ProxyTools,prepare_proxy_tools,
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
