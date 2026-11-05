"""No-external-dependency native FreeCAD collision contract tests."""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.freecad_native_collision import NativeStatus, classify_solids, prepare_solid, classify_prepared


def bbox(x0,x1):
    return SimpleNamespace(XMin=x0, XMax=x1, YMin=0.,YMax=10.,ZMin=0.,ZMax=10.)


class Shape:
    def __init__(self, box, volume=1000., common_volume=0., distance=0.):
        self.BoundBox=box
        self.Volume=volume
        self.common_volume=common_volume
        self.distance=distance
    def copy(self): return Shape(self.BoundBox, self.Volume, self.common_volume, self.distance)
    def isNull(self): return False
    def isValid(self): return True
    def isClosed(self): return True
    def common(self, other):
        return SimpleNamespace(Volume=self.common_volume)
    def distToShape(self, other):
        return self.distance, (), ()


class NativeCollisionTests(unittest.TestCase):
    def test_prepared_disjoint(self):
        a = prepare_solid(Shape(bbox(0.,10.)))
        b = prepare_solid(Shape(bbox(30.,40.)))
        r = classify_prepared(a, b)
        self.assertEqual(r.status, NativeStatus.CLEAR)

    def test_prepared_positive_overlap(self):
        a = prepare_solid(Shape(bbox(0.,10.), common_volume=200.))
        b = prepare_solid(Shape(bbox(8.,18.)))
        r = classify_prepared(a, b)
        self.assertEqual(r.status, NativeStatus.COLLISION)
        self.assertEqual(r.overlap_volume_mm3, 200.)

    def test_prepared_exact_contact_not_clear(self):
        a = prepare_solid(Shape(bbox(0.,10.)))
        b = prepare_solid(Shape(bbox(10.,20.)))
        self.assertEqual(classify_prepared(a,b).status, NativeStatus.UNVERIFIED)

    def test_prepared_input_rejected(self):
        self.assertEqual(classify_prepared(None,None).status, NativeStatus.UNVERIFIED)

    def test_prepared_snapshot_not_original(self):
        original = Shape(bbox(0.,10.))
        prepared = prepare_solid(original)
        self.assertIsNot(prepared.shape, original)
        original.BoundBox = bbox(90.,100.)
        self.assertEqual(prepared.bounds.XMin, 0.)

    def test_zero_volume_rejected_at_prepare(self):
        with self.assertRaises(ValueError):
            prepare_solid(Shape(bbox(0.,10.), volume=0.))

    def test_aabb_disjoint_skips_common(self):
        a=Shape(bbox(0.,10.))
        b=Shape(bbox(30.,40.))
        result=classify_solids(a,b)
        self.assertEqual(result.status, NativeStatus.CLEAR)
        self.assertEqual(result.reason, "aabb_separated")

    def test_positive_solid_overlap(self):
        a=Shape(bbox(0.,10.),common_volume=200.)
        b=Shape(bbox(8.,18.))
        r=classify_solids(a,b)
        self.assertEqual(r.status,NativeStatus.COLLISION)
        self.assertEqual(r.overlap_volume_mm3,200.)

    def test_contact_requires_explicit_contact_model(self):
        a=Shape(bbox(0.,10.),distance=0.)
        b=Shape(bbox(10.,20.))
        r=classify_solids(a,b)
        self.assertEqual(r.status,NativeStatus.UNVERIFIED)

    def test_near_gap_requires_policy(self):
        a=Shape(bbox(0.,10.),distance=.005)
        b=Shape(bbox(9.,20.))
        r=classify_solids(a,b)
        self.assertEqual(r.status,NativeStatus.UNVERIFIED)

    def test_invalid_shape_fails_closed(self):
        self.assertEqual(classify_solids(None,Shape(bbox(2.,12.))).status,
                         NativeStatus.UNVERIFIED)

    def test_kernel_exception_fails_closed(self):
        class Bad(Shape):
            def common(self,other):
                raise RuntimeError("kernel failed")
        r=classify_solids(Bad(bbox(0.,10.)),Shape(bbox(8.,18.)))
        self.assertEqual(r.status,NativeStatus.UNVERIFIED)
        self.assertIn("kernel failed",r.reason)


if __name__=="__main__":
    unittest.main()
