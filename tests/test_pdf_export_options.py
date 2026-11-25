from types import SimpleNamespace as NS
import unittest
from freecad_smbs.ui.pdf_export_options import (
    PdfOptions, face_layer, visible_faces, collision_box_faces, current_collision_hits,
)

class PdfOptionsTest(unittest.TestCase):
    def test_visibility_and_alpha(self):
        opt=PdfOptions()
        opt.visible["punch"]=False
        opt.transparency["die"]=100
        opt.transparency["part"]=50
        faces=[NS(kind=k) for k in ("punch_side","die_cap","panel_top","clamps","frame","collision")]
        self.assertEqual([face_layer(f) for f in visible_faces(faces,opt)],["part","clamps","frame"])
        self.assertEqual(opt.alpha("part"),128)
        self.assertTrue(PdfOptions().shows("punch"))
    def test_validation(self):
        for count in (0,11,True):
            with self.assertRaises(ValueError):
                PdfOptions(positions_per_page=count)
        with self.assertRaises(ValueError):
            PdfOptions(transparency={})
    def test_collision_box_machine_coordinates(self):
        faces=collision_box_faces(dict(xmin=10,xmax=20,ymin=30,ymax=40,zmin=50,zmax=60))
        self.assertEqual(len(faces),6)
        points={p for face in faces for p in face.vertices}
        self.assertEqual(points,{(-y,x,z) for x in (10,20) for y in (30,40) for z in (50,60)})
        self.assertFalse(collision_box_faces({}))
    def test_no_stale_mesh_findings(self):
        ws=NS(session=object(),_mesh_collision_identity="old",_mesh_preview_identity=lambda:"new",_mesh_collision_hits=({"operation":"B01"},))
        self.assertEqual(current_collision_hits(ws),())
        ws._mesh_collision_identity="new"
        self.assertEqual(len(current_collision_hits(ws)),1)
