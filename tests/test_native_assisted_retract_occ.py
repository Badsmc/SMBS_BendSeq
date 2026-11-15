"""Native FreeCAD/OCCT smoke tests; skipped under ordinary host Python."""
import unittest

try:
    import FreeCAD as App
    import Part
except ImportError:
    App = Part = None

from smbs_core.kinematics2d.native_assisted_retract import sample_synchronous_lift
from smbs_core.kinematics2d.native_collision_3d import validate_workpiece_tooling_pose


@unittest.skipUnless(App is not None and Part is not None, "requires native FreeCAD")
class NativeAssistedRetractTests(unittest.TestCase):
    def test_lift_uses_actual_occ_solids_without_mutating_inputs(self):
        sheet = Part.makeBox(2, 2, 1, App.Vector(0, 0, 2))
        punch = Part.makeBox(2, 2, 1, App.Vector(0, 0, 2.5))
        die = Part.makeBox(2, 2, 1, App.Vector(0, 0, 0))

        def moved(shape, dz):
            copy = shape.copy()
            copy.translate(App.Vector(0, 0, dz))
            return copy

        report = sample_synchronous_lift(
            sheet, punch, die, hit_ram_z_mm=1, bottom_ram_z_mm=-1,
            upper_ram_z_mm=3, samples=5, translate=moved,
            check=validate_workpiece_tooling_pose,
        )
        self.assertEqual(report["status"], "SAMPLED_LIFT_WITHOUT_PENETRATION_UNVERIFIED")
        self.assertEqual(len(report["samples"]), 5)
        self.assertEqual(sheet.BoundBox.ZMin, 2)
        self.assertEqual(punch.BoundBox.ZMin, 2.5)
        self.assertEqual(die.BoundBox.ZMin, 0)

        obstructing_die = Part.makeBox(2, 2, 1, App.Vector(0, 0, 1.5))
        blocked = sample_synchronous_lift(
            sheet, punch, obstructing_die,
            hit_ram_z_mm=1, bottom_ram_z_mm=-1, upper_ram_z_mm=3,
            samples=5, translate=moved, check=validate_workpiece_tooling_pose,
        )
        self.assertEqual(blocked["status"], "SAMPLED_LIFT_BLOCKED")
        self.assertEqual(blocked["samples"][0]["die"], "PENETRATION")


if __name__ == "__main__":
    unittest.main()
