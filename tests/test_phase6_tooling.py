import unittest
from smbs_core.models.tooling import Tooling, ToolingKind, DatumRole, Datum, AlignmentStatus, GeometryStatus, MachineConfiguration
from smbs_core.Physics.tooling_assembly import ToolingAssembly
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph, BendRecord

class TestPhase6Tooling(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.machine = PressBrakeMachine()

    def test_assembly_cannot_verify_missing_datums(self):
        assembly = ToolingAssembly(self.backend, self.machine.config)
        p_shape = self.backend.make_box(100, 50, 50)
        d_shape = self.backend.make_box(100, 60, 60)
        
        punch = Tooling("p1", ToolingKind.PUNCH, "p1.FCStd", p_shape, None, GeometryStatus.REAL, datums={})
        die = Tooling("d1", ToolingKind.DIE, "d1.FCStd", d_shape, None, GeometryStatus.REAL, datums={})

        res = assembly.place(punch, die)
        self.assertEqual(res.status, AlignmentStatus.CANNOT_VERIFY)
        self.assertIn("lacks verified datums", res.diagnostics[0])

    def test_cache_not_reused_across_tooling(self):
        bg = BendGraph()
        b1 = BendRecord("B1", "F1", "PA", "PB", (0, 0, 0), (1, 0, 0), 90.0, 2.0, 2.0)
        bg.add_bend(b1)

        state_config1 = FoldState.initial(None, bg, tooling_state={"punch_id": "punch_A", "die_id": "die_1"})
        state_config2 = FoldState.initial(None, bg, tooling_state={"punch_id": "punch_B", "die_id": "die_1"})

        self.assertNotEqual(state_config1.fingerprint(), state_config2.fingerprint())

    def test_machine_kinematics_configuration(self):
        machine = PressBrakeMachine("press_200t", max_tonnage=200.0, bed_length_mm=3000.0)
        self.assertEqual(machine.name, "press_200t")
        self.assertEqual(machine.max_tonnage, 200.0)
        self.assertEqual(machine.bed_length_mm, 3000.0)

if __name__ == "__main__":
    unittest.main()
