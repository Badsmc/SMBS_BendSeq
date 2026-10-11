import unittest
import FreeCAD, Part
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.models.tooling import ToolingKind

class TestInspectColl(unittest.TestCase):
    def test_inspect_coll(self):
        b1 = Part.makeBox(100, 100, 2)
        c1 = Part.makeCylinder(4, 100)
        c1.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), 90)
        c1.translate(FreeCAD.Vector(0, 100, 2))
        b2 = Part.makeBox(100, 50, 2)
        b2.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), 90)
        b2.translate(FreeCAD.Vector(0, 100, 2))
        uchannel = b1.fuse(c1).fuse(b2)
        uchannel.translate(FreeCAD.Vector(0, -100, -2))
        if uchannel.BoundBox.ZMin < 0:
            uchannel.translate(FreeCAD.Vector(0, 0, -uchannel.BoundBox.ZMin))
        m = PressBrakeMachine.default_setup()
        punch_shape = m.punch.shape if m.punch else None
        self.assertIsNotNone(m)

if __name__ == "__main__":
    unittest.main()
