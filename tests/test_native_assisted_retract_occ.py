"""Native FreeCAD/OCCT smoke tests; skipped under ordinary host Python."""
import unittest

try:
    import FreeCAD as App
    import Part
except ImportError:
    App = Part = None

from smbs_core.kinematics2d.native_assisted_retract import (
    sample_synchronous_lift, sample_lateral_release,
)
from smbs_core.kinematics2d.native_collision_3d import (
    validate_workpiece_tooling_pose, EndZoneCollisionPolicy,
)


@unittest.skipUnless(App is not None and Part is not None, "requires native FreeCAD")
class NativeAssistedRetractTests(unittest.TestCase):
    def test_native_release_reaches_disjoint_endpoint_without_mutating_sheet(self):
        sheet = Part.makeBox(2, 2, 1, App.Vector(0, 0, 1))
        punch = Part.makeBox(2, 4, 1, App.Vector(0, -1, 2))
        die = Part.makeBox(2, 4, 1, App.Vector(0, -1, -3))

        def moved(shape, dy):
            copy = shape.copy()
            copy.translate(App.Vector(0, dy, 0))
            return copy

        report = sample_lateral_release(
            sheet, punch, die, translate_y=moved,
            check=validate_workpiece_tooling_pose, samples=5,
        )
        self.assertEqual(report["status"], "SAMPLED_RELEASE_ROUTE_UNVERIFIED")
        self.assertEqual(set(report["candidate_directions_y"]), {-1, 1})
        self.assertEqual(sheet.BoundBox.YMin, 0)
        self.assertFalse(report["continuous_certification"])

    def test_selected_die_2d_stroke_matches_native_occ_kinematics(self):
        from pathlib import Path
        from types import SimpleNamespace
        from smbs_core.kinematics2d.native_tool_loader import load_native_die
        from smbs_core.kinematics2d.tooling_25d import ToolLibrary25D, ToolKind, ToolingSetup2D
        from smbs_core.kinematics2d.coupled_kinematics_2d import CoupledAirBendingKinematics

        library = Path(__file__).resolve().parents[1] / "toollibrary"
        die_id = "T-DIE_AT_3015_88°_V12_H80"
        punch_id = "PUNCH_AP_1017_88°_H104,65_R0,8"
        native = load_native_die(library / "die" / (die_id + ".FCStd"),
                                 virtual_length_mm=300)
        profiles = ToolLibrary25D(library)
        tooling = ToolingSetup2D()
        tooling.die = profiles.load_tool(die_id, ToolKind.DIE)
        tooling.punch = profiles.load_tool(punch_id, ToolKind.PUNCH)
        state = SimpleNamespace(bends={"B02": SimpleNamespace(
            angle_deg=90.0,
            bend_zone=SimpleNamespace(thickness_mm=1.5, inner_radius_mm=1.0),
        )})
        coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
            tooling, state, "B02")
        self.assertAlmostEqual(coupled.die_opening_v_mm,
                               native.effective_v_opening_mm, places=5)
        self.assertAlmostEqual(coupled.ram_z_for_angle(90), -3.492685, places=4)

    def test_exact_end_zone_overlap_remains_conditional(self):
        sheet = Part.makeBox(5, 2, 2, App.Vector(-50, 0, 0))
        punch = Part.makeBox(100, 2, 2, App.Vector(-50, 0, 0))
        die = Part.makeBox(100, 2, 2, App.Vector(-50, 0, -5))

        def moved(shape, dz):
            copy = shape.copy()
            copy.translate(App.Vector(0, 0, dz))
            return copy

        def check(workpiece, tool, lower):
            return validate_workpiece_tooling_pose(
                workpiece, tool, lower,
                end_zone_policy=EndZoneCollisionPolicy(True, 10),
                classify_end_zone_length_mm=10,
            )

        report = sample_synchronous_lift(
            sheet, punch, die, hit_ram_z_mm=0.5,
            bottom_ram_z_mm=0, upper_ram_z_mm=2,
            samples=5, translate=moved, check=check,
        )
        self.assertEqual(report["status"], "SAMPLED_LIFT_CONDITIONAL_UNVERIFIED")
        self.assertEqual(report["conditional_samples"], 5)
        self.assertTrue(all(x["central_overlap_mm3"] == 0 for x in report["samples"]))

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
