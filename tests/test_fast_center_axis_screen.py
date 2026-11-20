"""FAST sampled punch/die centreline filter regression suite.

Checks the geometric (X_m, Y_m, Z_m) contract, unverified fallbacks,
and beam pruning. Native FreeCAD tool solids are intentionally absent.
"""
from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.fast_center_axis_screen import (
    AxisScreenResult, _face_axis_slice, screen_transition_center_axis,
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
