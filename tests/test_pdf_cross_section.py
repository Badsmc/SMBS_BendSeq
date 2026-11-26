import unittest
from freecad.bendseq.ui.pdf_cross_section import face_segments, contours, section_x, section_contours
from freecad.bendseq.ui.pdf_export_options import PdfOptions, collision_box_faces
from freecad.bendseq.core.kinematics2d.preview_mesh_3d import PreviewFace3D


class PdfCrossSectionTest(unittest.TestCase):
    def test_machine_axis_and_overlap_box(self):
        hit={"overlap_bbox_xyz_mm":dict(xmin=10,xmax=20,ymin=30,ymax=40,zmin=50,zmax=60)}
        self.assertEqual(section_x(hit),15)
        opt=PdfOptions(right_view="section")
        opt.visible["collision"]=True
        groups=section_contours(collision_box_faces(hit["overlap_bbox_xyz_mm"]),15,opt)
        self.assertEqual(len(groups),1)
        path,closed=groups[0][1][0]
        self.assertTrue(closed)
        self.assertEqual(set(path),{(-30,50),(-30,60),(-40,50),(-40,60)})
        self.assertFalse(section_contours(collision_box_faces(hit["overlap_bbox_xyz_mm"]),0,opt))

    def test_hole_is_not_filled_by_face_cut(self):
        face=PreviewFace3D(((0,-3,0),(10,-3,0),(10,3,0),(0,3,0)),
                           "panel_top","P",holes=(((3,-1,0),(7,-1,0),(7,1,0),(3,1,0)),))
        self.assertEqual(face_segments(face,0),(((0.0,0.0),(3.0,0.0)),((7.0,0.0),(10.0,0.0))))

    def test_open_contour_is_not_closed(self):
        self.assertEqual(contours([((0,0),(1,0)),((1,0),(2,0))])[0][1],False)

    def test_hidden_collision_and_old_settings(self):
        opt=PdfOptions()
        self.assertEqual(opt.right_view,"3d")
        self.assertFalse(opt.shows("collision"))
        with self.assertRaises(ValueError):
            PdfOptions(right_view="invalid")


    def test_disconnected_exact_overlap_selects_a_nonempty_cut(self):
        hit={"overlap_bbox_xyz_mm":dict(xmin=-20,xmax=20)}
        faces=(
            PreviewFace3D(((0,-20,0),(1,-20,0),(0,-10,0)), "collision","overlap_solid"),
            PreviewFace3D(((0,10,0),(1,10,0),(0,20,0)), "collision","overlap_solid"),
        )
        x=section_x(hit,faces)
        self.assertIn(x,(-15,15))
        self.assertTrue(any(face_segments(face,x) for face in faces))
