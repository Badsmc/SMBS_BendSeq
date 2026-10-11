import unittest
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.models.tooling import Tooling, ToolingKind, Datum, DatumRole, MachineConfiguration, GeometryStatus
from smbs_core.Physics.tooling_assembly import ToolingAssembly
from smbs_core.Physics.machine_kinematics import PressBrakeMachine

class TestCollisionsFixed(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.machine = PressBrakeMachine()
        
        self.assembly = ToolingAssembly(self.backend, self.machine.config)
        
        punch = Tooling(
            id="MockPunch", kind=ToolingKind.PUNCH, source_file="", shape="PunchShape",
            local_frame=None, geometry_status=GeometryStatus.REAL, diagnostics=[]
        )
        punch.datums[DatumRole.MOUNTING_PLANE] = Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 120), (0, 0, 1)), confirmed=True)
        punch.datums[DatumRole.TIP_POINT] = Datum(DatumRole.TIP_POINT, "point", ((0, 0, 0),), confirmed=True)
        punch.datums[DatumRole.WORKING_AXIS] = Datum(DatumRole.WORKING_AXIS, "axis", ((0, 0, 0), (1, 0, 0)), confirmed=True)
        
        die = Tooling(
            id="MockDie", kind=ToolingKind.DIE, source_file="", shape="DieShape",
            local_frame=None, geometry_status=GeometryStatus.REAL, diagnostics=[]
        )
        die.datums[DatumRole.MOUNTING_PLANE] = Datum(DatumRole.MOUNTING_PLANE, "plane", ((0, 0, 0), (0, 0, -1)), confirmed=True)
        die.datums[DatumRole.V_CENTER] = Datum(DatumRole.V_CENTER, "axis", ((0, 0, 80), (1, 0, 0)), confirmed=True)
        die.datums[DatumRole.WORKING_AXIS] = Datum(DatumRole.WORKING_AXIS, "axis", ((0, 0, 80), (1, 0, 0)), confirmed=True)
        
        self.assembly.place(punch, die)
        self.machine.assembly = self.assembly
        self.detector = CollisionDetector(self.machine, self.backend)

    def test_missing_geometry_intersection(self):
        res = self.detector._check_intersection(None, "Tool")
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)
        
        res = self.detector._check_intersection("Part", None)
        self.assertEqual(res.status, CollisionStatus.UNKNOWN)

if __name__ == "__main__":
    unittest.main()
