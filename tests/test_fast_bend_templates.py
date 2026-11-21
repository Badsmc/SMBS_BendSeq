"""FAST reusable operator recipes: geometric matching, safety and persistence."""
from __future__ import annotations

from math import radians, sin, cos
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest

from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.staged_bends import BendOperation
from smbs_core.kinematics2d.fast_bend_templates import (
    BendTemplateError, geometry_identity, store_operator_template,
    find_operator_templates,
)


def demo(dx=0., dy=0., theta=0., prefix="B", panel_prefix="P",
         modified_outline=False, modified_angle=False):
    t = radians(theta)
    def xy(p):
        x, y = p
        return (x*cos(t)-y*sin(t)+dx, x*sin(t)+y*cos(t)+dy)
    def xyz(p):
        x, y = xy(p)
        return (x,y,0.)
    corners = ((0.,0.),(120.,0.),(120.,80.),(0.,80.))
    if modified_outline:
        corners = ((0.,0.),(125.,0.),(125.,80.),(0.,80.))
    outer = tuple(xy(p) for p in corners)
    raw_panels = (
        ((0.,0.),(40.,0.),(40.,80.),(0.,80.)),
        ((40.,0.),(80.,0.),(80.,80.),(40.,80.)),
        ((80.,0.),(120.,0.),(120.,80.),(80.,80.)),
    )
    panels = {
        panel_prefix+str(i):tuple(xy(pt) for pt in points)
        for i,points in enumerate(raw_panels)
    }
    bends = []
    for i,x in enumerate((40.,80.)):
        name = prefix+str(i+1)
        axis = (xyz((x,0.)),xyz((x,80.)))
        zone = SimpleNamespace(
            thickness_mm=1.5,inner_radius_mm=1.0,
            outer_radius_mm=2.5,
        )
        bends.append(GraphEdge(
            name,panel_prefix+str(i),panel_prefix+str(i+1),
            88. if modified_angle and i==0 else 90.,
            axis,zone,
        ))
    fold = FoldState(PanelGraph(tuple(panels),tuple(bends)),panel_prefix+"0")
    ops = tuple(BendOperation(edge.bend_id,edge.bend_id,edge.angle_deg)
                for edge in bends)
    return fold,ops,outer,panels


class TemplateTests(unittest.TestCase):
    def test_exact_geometry_matches_renumbered_rotated_translated_part(self):
        old = demo()
        new = demo(dx=512.7,dy=-121.3,theta=90.,
                   prefix="B9",panel_prefix="K")
        ident_a = geometry_identity(*old)
        ident_b = geometry_identity(*new)
        self.assertEqual(ident_a.digest, ident_b.digest)
        self.assertEqual(ident_a.bend_count,2)
        with TemporaryDirectory() as folder:
            path=Path(folder)/"recipes.json"
            record=store_operator_template(
                *old,("B2","B1"),{"B1":True,"B2":False},
                title="Проверенный порядок",
                tooling={"punch":"P1","die":"D1"},path=path,
            )
            self.assertTrue(path.exists())
            self.assertEqual(record["evidence"],
                             "OPERATOR_RECORDED_NOT_MACHINE_CERTIFIED")
            matches=find_operator_templates(*new,path=path)
            self.assertEqual(len(matches),1)
            self.assertEqual(matches[0].candidate.operation_ids,("B92","B91"))
            self.assertTrue(matches[0].candidate.orientations()["B91"])
            self.assertFalse(matches[0].candidate.orientations()["B92"])
            self.assertIn("UNVERIFIED",matches[0].candidate.evidence)
            self.assertEqual(matches[0].source_tooling["punch"],"P1")

    def test_changed_outline_or_angle_never_reuses_old_recipe(self):
        old=demo()
        with TemporaryDirectory() as folder:
            path=Path(folder)/"patterns.json"
            store_operator_template(
                *old,("B1","B2"),{},title="test",path=path,
            )
            for changed in (
                demo(modified_outline=True),
                demo(modified_angle=True),
            ):
                self.assertFalse(find_operator_templates(*changed,path=path))

    def test_duplicate_title_replaces_entry_without_silent_auto_approval(self):
        part=demo()
        with TemporaryDirectory() as folder:
            path=Path(folder)/"templates.json"
            store_operator_template(
                *part,("B1","B2"),{},title="checked",path=path,
            )
            store_operator_template(
                *part,("B2","B1"),{"B2":True},
                title="checked",path=path,
            )
            self.assertEqual(len(json.loads(path.read_text("utf-8"))["templates"]),1)
            matches=find_operator_templates(*part,path=path)
            self.assertEqual(matches[0].candidate.operation_ids,("B2","B1"))
            self.assertTrue(matches[0].candidate.orientations()["B2"])

    def test_requires_complete_noninvented_order_and_cannot_read_wrong_schema(self):
        part=demo()
        with TemporaryDirectory() as folder:
            path=Path(folder)/"patterns.json"
            with self.assertRaises(BendTemplateError):
                store_operator_template(
                    *part,("B1",),{},title="partial",path=path,
                )
            path.write_text('{"schema":"unknown","templates":[]}')
            with self.assertRaises(BendTemplateError):
                find_operator_templates(*part,path=path)
            self.assertEqual(path.read_text(),'{"schema":"unknown","templates":[]}')

    def test_nonmatching_mirror_is_not_automatically_accepted(self):
        old=demo()
        # Horizontal mirror changes physical handedness, not a rigid rotation.
        state,steps,outline,panels=old
        def reflect(p):
            return (-p[0],p[1])
        edges=[]
        for edge in state.graph.edges:
            axis=tuple((*reflect(pt),0.) for pt in edge.axis_xyz)
            edges.append(GraphEdge(
                edge.bend_id,edge.panel_a,edge.panel_b,
                edge.angle_deg,axis,edge.bend_zone,
            ))
        reflected=(
            FoldState(PanelGraph(tuple(panels),tuple(edges)),"P0"),
            steps,tuple(reflect(p) for p in outline),
            {k:tuple(reflect(p) for p in pts)
             for k,pts in panels.items()},
        )
        self.assertNotEqual(
            geometry_identity(*old).digest,
            geometry_identity(*reflected).digest,
        )


if __name__=="__main__":
    unittest.main()
