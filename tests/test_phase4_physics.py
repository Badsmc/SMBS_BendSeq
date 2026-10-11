"""
test_phase4_physics.py - Test Machine Kinematics (Updated for Phase 5 Tooling Assembly)
"""
import unittest
from smbs_core.Physics.machine_kinematics import PressBrakeMachine

class TestPhase4Physics(unittest.TestCase):
    def test_machine_instantiation(self):
        machine = PressBrakeMachine(name="TestPress")
        self.assertEqual(machine.name, "TestPress")
        self.assertEqual(machine.config.id, "TestPress")
        self.assertEqual(machine.config.max_tonnage_kn, 1000.0) # 100T -> 1000 kN

if __name__ == '__main__':
    unittest.main()
