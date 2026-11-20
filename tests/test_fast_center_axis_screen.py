"""FAST sampled punch/die centreline filter regression suite.

Checks the geometric (X_m, Y_m, Z_m) contract, unverified fallbacks,
and beam pruning. Native FreeCAD tool solids are intentionally absent.
"""
from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.fast_center_axis_screen import (
    AxisScreenResult, _face_axis_slice, _dominant_panel,
    _dominant_panel_axis_intrusion, screen_transition_center_axis,
)
from smbs_core.kinematics2d.fast_bfm_beam_search import search_fast_candidates
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.bend_zone import BendParameters, make_bend_zone


def one_bend_with_zone():
    angle=90.0
    axis=((0.,0.,0.),(50.,0.,0.))
    params=BendParameters(angle,1.5,.3,1.0,1.45,2.5,2.278)
    zone=make_bend_zone("B01", ((0.,0.),(50.,0.)),params)
    graph=PanelGraph(
        ("P0","P1"),
        (GraphEdge("B01","P1","P0",angle,axis,zone),),
    )
    sheet=FoldState(graph,"P0")
    outlines={
        "P0":((0.,-32.),(50.,-32.),(50.,-1.2),(0.,-1.2)),
        "P1":((0.,1.2),(50.,1.2),(50.,45.),(0.,45.)),
    }
    return sheet,outlines


class CentrelinePolygonTests(TestCase):
    def test_unambiguous_off_zone_sheet_crossing_is_blocked(self):
        face=[(-20.,-10.,30.),(20.,-10.,30.),
              (20.,10.,30.),(-20.,10.,30.)]
        self.assertEqual(
            _face_axis_slice(face,-15,15,0.0,4.0), (0.0,30.0),
        )

    def test_intended_contact_near_axis_is_exempt(self):
        face=[(-20.,-10.,2.),(20.,-10.,2.),
              (20.,10.,2.),(-20.,10.,2.)]
        self.assertIsNone(_face_axis_slice(face,-15,15,0.0,4.0))

    def test_outside_active_tool_span_not_rejected(self):
        face=[(30.,-10.,50.),(50.,-10.,50.),
              (50.,10.,50.),(30.,10.,50.)]
        self.assertIsNone(_face_axis_slice(face,-15,15,0.0,4.0))

    def test_concave_polygon_open_channel_is_not_material(self):
        # Line Y=0 crosses the two side legs, not the central notch.
        face=[(-20.,-5.,30.),(-10.,-5.,30.),(-10.,5.,30.),
              (10.,5.,30.),(10.,-5.,30.),(20.,-5.,30.),
              (20.,10.,30.),(-20.,10.,30.)]
        self.assertIsNone(_face_axis_slice(face,-5,5,0.0,4.0))

    def test_tangency_or_ambiguous_vertex_not_proven_blocker(self):
        face=[(-20.,0.,30.),(20.,-10.,30.),(20.,10.,30.)]
        self.assertIsNone(_face_axis_slice(face,-15,15,0.0,4.0))


    def test_small_side_flange_crossing_is_allowed(self):
        # Short 8 mm intersection within a 40 mm bend is not proof that
        # the main sheet is incorrectly threaded through the centre axis.
        strip=[(-4.,-5.,40.),(4.,-5.,40.),
               (4.,5.,40.),(-4.,5.,40.)]
        self.assertIsNotNone(_face_axis_slice(strip,-20,20,0.,12.))
        self.assertIsNone(
            _dominant_panel_axis_intrusion(strip,-20,20,0.,12.)
        )

    def test_gross_main_body_span_is_suspicious_only(self):
        main=[(-20.,-5.,35.),(20.,-5.,35.),
              (20.,5.,35.),(-20.,5.,35.)]
        hit=_dominant_panel_axis_intrusion(main,-20,20,0.,12.)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[2],1.0)

    def test_main_panel_requires_majority_of_flat_sheet_area(self):
        panels={
            "main": ((0.,0.),(70.,0.),(70.,30.),(0.,30.)),
            "small":((0.,0.),(10.,0.),(10.,10.),(0.,10.)),
        }
        self.assertEqual(
            _dominant_panel(panels,("main","small")),"main"
        )
        ambiguous={
            "a":((0.,0.),(20.,0.),(20.,20.),(0.,20.)),
            "b":((0.,0.),(20.,0.),(20.,20.),(0.,20.)),
            "c":((0.,0.),(20.,0.),(20.,20.),(0.,20.)),
        }
        self.assertIsNone(_dominant_panel(ambiguous,("a","b","c")))


class CentrelineIntegrationTests(TestCase):
    def test_real_bend_zone_runs_without_mutating_fold_state(self):
        sheet, outlines=one_bend_with_zone()
        from smbs_core.kinematics2d.staged_bends import BendOperation
        before=dict(sheet.angles)
        result=screen_transition_center_axis(
            sheet,outlines,(BendOperation("B01","B01",90.),),0,0,
        )
        self.assertEqual(result.decision,"CLEAR_SAMPLED",result)
        self.assertEqual(result.samples,3)
        self.assertEqual(sheet.angles,before)

    def test_single_phase_gross_intersection_does_not_prune(self):
        sheet, outlines=one_bend_with_zone()
        from smbs_core.kinematics2d.staged_bends import BendOperation
        op=(BendOperation("B01","B01",90.),)
        calls=[0]
        def one_phase(polygon,xmin,xmax,reference,excluded):
            calls[0]+=1
            return (25.,reference+30.,.95) if calls[0]<=2 else None
        with patch(
            "smbs_core.kinematics2d.fast_center_axis_screen."
            "_dominant_panel_axis_intrusion",
            side_effect=one_phase,
        ):
            result=screen_transition_center_axis(sheet,outlines,op,0,0)
        self.assertEqual(result.decision,"UNKNOWN",result)
        self.assertEqual(result.samples,3)

    def test_persistent_main_body_crossing_above_is_rejected(self):
        sheet, outlines=one_bend_with_zone()
        from smbs_core.kinematics2d.staged_bends import BendOperation
        op=(BendOperation("B01","B01",90.),)
        def gross(polygon,xmin,xmax,reference,excluded):
            return (25.,reference+30.,.9)
        with patch(
            "smbs_core.kinematics2d.fast_center_axis_screen."
            "_dominant_panel_axis_intrusion",
            side_effect=gross,
        ):
            result=screen_transition_center_axis(sheet,outlines,op,0,0)
        self.assertEqual(result.decision,"BLOCKED",result)
        self.assertEqual(result.panel_id,"P1")
        self.assertIn("GROSS_MAIN_BODY_POSE",result.reason)
        self.assertIn("ABOVE",result.reason)
        self.assertEqual(result.samples,2)

    def test_above_once_below_once_is_not_persistent_same_side(self):
        sheet, outlines=one_bend_with_zone()
        from smbs_core.kinematics2d.staged_bends import BendOperation
        op=(BendOperation("B01","B01",90.),)
        calls=[0]
        def alternating(polygon,xmin,xmax,reference,excluded):
            calls[0]+=1
            if calls[0]<=2:
                return (25.,reference+30.,.9)
            if calls[0]<=4:
                return (25.,reference-30.,.9)
            return None
        with patch(
            "smbs_core.kinematics2d.fast_center_axis_screen."
            "_dominant_panel_axis_intrusion",
            side_effect=alternating,
        ):
            result=screen_transition_center_axis(sheet,outlines,op,0,0)
        self.assertEqual(result.decision,"UNKNOWN",result)

    def test_missing_outlines_never_pruned(self):
        sheet,outlines=one_bend_with_zone()
        from smbs_core.kinematics2d.staged_bends import BendOperation
        del outlines["P1"]
        result=screen_transition_center_axis(
            sheet,outlines,(BendOperation("B01","B01",90.),),0,0,
        )
        self.assertEqual(result.decision,"UNKNOWN")

    def test_search_prunes_blocked_transition_for_both_180_orientations(self):
        bends=[]
        for i in range(3):
            axis=((float(i)*10,0.,0.),(float(i)*10+50.,0.,0.))
            bends.append(GraphEdge(
                f"B{i+1:02d}",f"P{i}",f"P{i+1}",90.,axis,
            ))
        state=FoldState(PanelGraph(
            tuple(f"P{i}" for i in range(4)),tuple(bends),
        ),"P0")
        # Substitute zone marker only to enable FAST axis screening.
        # The integration mock returns deterministic result instead of
        # calling machine placement on this synthetic mock geometry.
        state.bends={
            bid:replace(b,bend_zone=object())
            for bid,b in state.bends.items()
        }
        outlines={
            pid:((-20.,-20.),(60.,-20.),(60.,20.),(-20.,20.))
            for pid in state.graph.panels
        }
        def fake_checker(source,vertices,steps,mask,index,**kwargs):
            op=steps[index]
            # B01 is physically blocked if B02 has already been performed.
            if op.bend_id=="B01" and mask & (1<<1):
                return AxisScreenResult("BLOCKED","synthetic_axis_cross",samples=3)
            return AxisScreenResult("CLEAR_SAMPLED","mock_clear",samples=3)
        with patch(
            "smbs_core.kinematics2d.fast_center_axis_screen."
            "screen_transition_center_axis",
            side_effect=fake_checker,
        ) as mocked:
            report=search_fast_candidates(
                state, panel_vertices_xy=outlines,
                beam_width=48, max_candidates=5,
            )
        self.assertTrue(mocked.called)
        self.assertTrue(report.candidates)
        self.assertTrue(report.axis_screen_available)
        self.assertGreater(report.axis_screen_rejected,0)
        self.assertGreater(report.axis_screen_checks,0)
        self.assertGreater(report.axis_screen_samples,0)
        for choice in report.candidates:
            self.assertLess(
                choice.operation_ids.index("B01"),
                choice.operation_ids.index("B02"),
            )
            self.assertEqual(choice.evidence,"UNVERIFIED_3D_NOT_CHECKED")

    def test_axis_filter_must_remain_optional_on_missing_geometry(self):
        state,outlines=one_bend_with_zone()
        report=search_fast_candidates(state, panel_vertices_xy=None)
        self.assertFalse(report.axis_screen_available)
        self.assertEqual(report.axis_screen_rejected,0)
        self.assertTrue(all(c.evidence.startswith("UNVERIFIED")
                            for c in report.candidates))


if __name__=="__main__":
    import unittest
    unittest.main()
