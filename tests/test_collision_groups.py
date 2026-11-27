import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from freecad.bendseq.core.kinematics2d import collision_groups as cg
from freecad.bendseq.core.kinematics2d.workpiece_placement_3d import PlacementTransform3D

I=((1,0,0),(0,1,0),(0,0,1))
R=((0,-1,0),(1,0,0),(0,0,1))
B=dict(xmin=0,xmax=2,ymin=0,ymax=1,zmin=0,zmax=1)

class CollisionGroupsTest(unittest.TestCase):
    def test_groups_keep_stages_separate_and_sequence_order(self):
        hits=[dict(operation=k,conditional=c,occ_overlap_volume_mm3=v)
              for k,c,v in (("B02",True,50),("B01",False,2),("B02",False,1),("B01.2",False,4))]
        groups=cg.collision_groups(hits,operation_order=("B01","B01.2","B02"))
        self.assertEqual([g[0]["operation"] for g in groups],["B01","B01.2","B02"])
        self.assertEqual(len(groups[-1]),2)
        self.assertIs(cg.representative_hit(groups[-1]),hits[2])
        self.assertEqual(len(cg.collision_groups(hits,grouped=False)),4)

    def test_rigid_mapping_keeps_oriented_corners(self):
        src=PlacementTransform3D(I,(10,0,0))
        dst=PlacementTransform3D(R,(0,20,0))
        corners=cg.bbox_corners(B)
        moved=cg.remap_corners(corners,src,dst)
        for a,b in zip(corners,moved):
            self.assertEqual(b,(-a[1],a[0]+10,a[2]))
        self.assertEqual(len(cg.BOX_EDGES),12)

    def test_changed_bend_zone_uses_component_not_false_box(self):
        ref=dict(component="P",operation="B01",phase="BOTTOM",overlap_bbox_xyz_mm=B)
        hit=dict(component="B01",operation="B01",phase="BENDING",conditional=True,overlap_bbox_xyz_mm=B)
        state=lambda angle:NS(transforms={"P":NS(rotation=I,translation=(0,0,0))},
            bends={"B01":NS(fixed_panel="P")},angles={"B01":angle})
        place=PlacementTransform3D(I,(0,0,0))
        with patch.object(cg,"sample_state",side_effect=[(state(90),place),(state(45),place)]):
            zones=cg.projected_zones(None,None,(ref,hit),ref)
        self.assertTrue(zones[1]["component_only"])
        self.assertEqual(cg.component_colors(zones),{"B01":2})
        self.assertEqual(cg.component_colors(zones,all_components=True),{"P":1,"B01":2})
