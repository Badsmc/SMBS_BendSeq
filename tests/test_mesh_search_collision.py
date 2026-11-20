import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.mesh_search_collision import Tree, MeshOracle, mesh_search_mode, _MODE, triangle_distance2


def cube(size=10):
    p=[(0,0,0),(size,0,0),(size,size,0),(0,size,0),
       (0,0,size),(size,0,size),(size,size,size),(0,size,size)]
    f=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
       (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return p,f


class MeshTests(unittest.TestCase):
    def test_depth_allowance_and_outside(self):
        p,f=cube();t=Tree([tuple(p[i] for i in ids) for ids in f])
        self.assertFalse(t.deep_inside((0.9,5,5),1))
        self.assertTrue(t.deep_inside((1.1,5,5),1))
        self.assertFalse(t.deep_inside((-1,5,5),1))
        self.assertFalse(t.deep_inside((0,5,5),1))

    def test_triangle_distance(self):
        self.assertAlmostEqual(triangle_distance2((1,1,2),((0,0,0),(4,0,0),(0,4,0))),4)

    def test_context_restored_even_on_exception(self):
        self.assertIsNone(_MODE.get())
        with self.assertRaises(ValueError):
            with mesh_search_mode(MeshOracle()):raise ValueError()
        self.assertIsNone(_MODE.get())

    def test_cache_and_closed_mesh(self):
        p,f=cube();calls=[]
        shape=SimpleNamespace(hashCode=lambda:42,Placement='identity',
            tessellate=lambda d:(calls.append(d) or [SimpleNamespace(x=x,y=y,z=z) for x,y,z in p],f))
        oracle=MeshOracle();oracle.mesh(shape);oracle.mesh(shape)
        self.assertEqual(len(calls),1)
        shape.hashCode=lambda:43
        shape.tessellate=lambda d:([SimpleNamespace(x=x,y=y,z=z) for x,y,z in p],f[:-1])
        with self.assertRaises(ValueError):oracle.mesh(shape)

    def test_mesh_blocker_does_not_fabricate_occ_volume(self):
        from smbs_core.kinematics2d.motion_trajectory_3d import is_blocking_collision
        from smbs_core.kinematics2d.native_collision_3d import CollisionClass
        pair=SimpleNamespace(status=CollisionClass.PENETRATION,overlap_volume_mm3=0,
                             reason='MESH_SAMPLED_DEPTH_GT_ALLOWANCE')
        state=SimpleNamespace(is_safe=False,is_conditional_safe=False,collision_report=SimpleNamespace(
            status=CollisionClass.PENETRATION,punch_result=pair,die_result=None))
        self.assertFalse(is_blocking_collision(state))
        with mesh_search_mode(MeshOracle()):self.assertTrue(is_blocking_collision(state))
        self.assertFalse(is_blocking_collision(state))

    def test_actual_pair_deep_penetration_and_shallow_allowance(self):
        def shape(key, x):
            p,f=cube()
            return SimpleNamespace(hashCode=lambda:key,Placement=str(x),
                tessellate=lambda d:([SimpleNamespace(x=a+x,y=b,z=c) for a,b,c in p],f))
        oracle=MeshOracle(1,0.1)
        self.assertFalse(oracle.collision(shape(1,0),shape(2,9.2)))
        self.assertTrue(oracle.collision(shape(1,0),shape(3,7)))
