"""The opt-in lift preview follows the sampled OCC ram path exactly."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.press_cycle import (
    CycleExecutionMode, CyclePhase, PressCycleController,
)
from smbs_core.kinematics2d.coupled_kinematics_2d import effective_die_opening_from_profile
from smbs_core.kinematics2d.native_assisted_retract import compare_preview_lift_bounds
from smbs_core.kinematics2d.tooling_25d import ArcSegment2D


class AssistedRetractPreviewTests(unittest.TestCase):
    def test_real_die_fillet_centers_match_native_v_opening(self):
        arcs = tuple(ArcSegment2D(
            start=(x, 0.0), end=(x + (1 if x < 0 else -1), -1),
            center=(x, -2.7499998286), radius=2.75,
        ) for x in (-7.1673060812, 7.167305408))
        # The imported profile has its top datum at zero. Its shoulder
        # centers are exactly those measured from the native CAD BRep.
        die = SimpleNamespace(
            die_datums=SimpleNamespace(shoulder_radius_mm=2.75),
            local_geometry=SimpleNamespace(outer_contour=SimpleNamespace(segments=arcs)),
        )
        self.assertAlmostEqual(effective_die_opening_from_profile(die, 12),
                               14.3346114892, places=6)

    def test_native_bounds_disagreement_disables_preview(self):
        coupled = SimpleNamespace(ram_z_for_angle=lambda a: -2.325379,
                                  contact_z_mm=1.5)
        report = {"bottom_ram_z_mm": -3.492685, "upper_ram_z_mm": 6.5}
        self.assertFalse(compare_preview_lift_bounds(report, coupled, 90, 5)["matched"])
        coupled.ram_z_for_angle = lambda a: -3.492685
        self.assertTrue(compare_preview_lift_bounds(report, coupled, 90, 5)["matched"])

    def setUp(self):
        self.pc = object.__new__(PressCycleController)
        self.pc.sequence = SimpleNamespace(
            active_bend_id="B02", target_angle_deg=90.0,
            fold_state=SimpleNamespace(bends={
                "B02": SimpleNamespace(bend_zone=SimpleNamespace(outer_radius_mm=2.5)),
            }),
        )
        self.pc.coupled_kinematics = SimpleNamespace(stroke_for_angle=lambda angle: 6.0)
        self.pc.execution_mode = CycleExecutionMode.FULL_CYCLE
        self.pc.assisted_retract_preview_bends = {"B02"}
        self.pc.bottom_z_mm = -3.5
        self.pc.upper_z_mm = 6.5
        self.pc.ram_z_mm = -3.5
        self.pc._manual_preview_active = False
        self.pc.loading_orientation_by_bend = {}
        self.pc.rear_panel_vertices_xy = None
        self.pc.transfer_fault = None
        self.pc.phase = CyclePhase.BOTTOM

    def support(self):
        with patch("smbs_core.kinematics2d.press_cycle.WorkpiecePlacement3D.compute_placement",
                   return_value=None) as placement:
            self.pc.current_workpiece_placement_3d()
            return placement.call_args.kwargs["support_plane_zm"]

    def test_bottom_retract_completion_share_a_continuous_path(self):
        bottom_support = self.support()
        for phase, ram in ((CyclePhase.RETRACT, -3.5),
                           (CyclePhase.RETRACT, 1.5),
                           (CyclePhase.RETRACT, 6.5),
                           (CyclePhase.STEP_COMPLETE, 6.5),
                           (CyclePhase.COMPLETED, 6.5)):
            self.pc.phase, self.pc.ram_z_mm = phase, ram
            self.assertAlmostEqual(self.support() - bottom_support, ram + 3.5)
            self.assertAlmostEqual(self.pc.assisted_retract_lift_mm(), ram + 3.5)

    def test_only_selected_bend_and_full_cycle_can_lift(self):
        self.pc.phase, self.pc.ram_z_mm = CyclePhase.RETRACT, 1.5
        baseline = self.support()
        self.pc.assisted_retract_preview_bends.clear()
        self.assertAlmostEqual(self.support(), baseline - 5.0)
        self.pc.assisted_retract_preview_bends.add("B02")
        self.pc.execution_mode = CycleExecutionMode.BEND_ONLY
        self.assertEqual(self.pc.assisted_retract_lift_mm(), 0.0)

    def test_never_lifts_during_bending_or_before_bottom(self):
        for phase in (CyclePhase.IDLE, CyclePhase.APPROACH,
                      CyclePhase.CONTACT, CyclePhase.BENDING, CyclePhase.BOTTOM):
            self.pc.phase = phase
            self.pc.ram_z_mm = 1.5
            self.assertEqual(self.pc.assisted_retract_lift_mm(), 0.0)


if __name__ == "__main__":
    unittest.main()
