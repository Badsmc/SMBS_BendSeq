"""Headless regressions: shared preview/native Z and honest RETRACT evidence."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.manual_physical_validation import (
    retract_evidence_from_trajectory,
)
from smbs_core.kinematics2d.ram_cycle_geometry import ram_cycle_positions
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
from smbs_core.kinematics2d.press_cycle import CycleConfig


def sample(u, z, *, collision=False):
    col = SimpleNamespace(status=SimpleNamespace(
        value="PENETRATION" if collision else "CLEAR",
    ))
    return SimpleNamespace(
        path_u=u, ram_z_mm=z, collision_report=col,
    )


def fake_trajectory(
    status="SAMPLED_INTERVAL_CONVERGED",
    *,
    states=None,
    planned=None,
    with_retract=True,
    unresolved=False,
):
    if planned is None:
        planned = ram_cycle_positions(3.0, -7.0, 12.0)
    phases = []
    if with_retract:
        phases = [
            SimpleNamespace(
                phase_name="RETRACT",
                states=states if states is not None
                    else (sample(0, -7), sample(1, 15)),
                evidence_status=SimpleNamespace(value=status),
                diagnostics=["native diagnostic"],
                unresolved_intervals=[(0.3, 0.4)] if unresolved else [],
                uninspected_intervals=[],
                discontinuities=[],
                max_punch_overlap_volume_mm3=1.25 if "REJECTED" in status else 0,
                max_die_overlap_volume_mm3=0.0,
            )
        ]
    return SimpleNamespace(
        phase_results=phases,
        ram_cycle_positions_mm=planned,
    )


class RetractPathTests(unittest.TestCase):
    def test_configured_upper_includes_contact_z_not_absolute_40_or_50(self):
        values = ram_cycle_positions(3.0, -7.0, 12.0)
        self.assertEqual(values["approach_start_z_mm"], 15.0)
        self.assertEqual(values["retract_end_z_mm"], 15.0)
        self.assertEqual(values["retract_start_z_mm"], -7.0)
        self.assertEqual(values["contact_z_mm"], 3.0)
        self.assertEqual(values["initial_clearance_mm"], 12.0)

    def test_clearance_default_matches_preview_and_native_config(self):
        self.assertEqual(
            CycleConfig().initial_clearance_mm,
            AdaptiveSamplingConfig().initial_clearance_mm,
        )

    def test_nonfinite_and_inverted_machine_bounds_rejected(self):
        for inputs in (
            (0, -1, 0), (0, -1, float("nan")),
            (0, 1, 5), (float("inf"), -1, 5),
        ):
            with self.subTest(inputs=inputs):
                with self.assertRaises(ValueError):
                    ram_cycle_positions(*inputs)

    def test_good_retract_saved_with_machine_z_and_actual_samples(self):
        result = retract_evidence_from_trajectory(fake_trajectory())
        self.assertEqual(result["status"], "SAMPLED_CLEAR")
        self.assertEqual(result["start_z_mm"], -7.0)
        self.assertEqual(result["end_z_mm"], 15.0)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["sampled_start_z_mm"], -7.0)
        self.assertEqual(result["sampled_end_z_mm"], 15.0)
        self.assertTrue(result["end_position_sampled"])

    def test_retract_not_reached_is_not_a_pass(self):
        result = retract_evidence_from_trajectory(
            fake_trajectory(with_retract=False),
        )
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertEqual(result["start_z_mm"], -7.0)
        self.assertEqual(result["end_z_mm"], 15.0)
        self.assertFalse(result["end_position_sampled"])

    def test_early_stop_inside_retract_is_unverified(self):
        result = retract_evidence_from_trajectory(fake_trajectory(
            states=(sample(0, -7), sample(.45, 3)),
            unresolved=True,
        ))
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["sample_count"], 2)

    def test_missing_initial_sample_is_not_a_clear_retract(self):
        result = retract_evidence_from_trajectory(fake_trajectory(
            states=(sample(.5, 4.0), sample(1, 15.0)),
        ))
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertFalse(result["start_position_sampled"])
        self.assertTrue(result["end_position_sampled"])

    def test_mismatched_bottom_sample_is_not_a_clear_retract(self):
        result = retract_evidence_from_trajectory(fake_trajectory(
            states=(sample(0, -2), sample(1, 15)),
        ))
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertIn("does not match", result["reason"])

    def test_phase_marked_unsafe_cannot_override_native_verdict(self):
        trajectory = fake_trajectory()
        trajectory.phase_results[0].is_safe = False
        result = retract_evidence_from_trajectory(trajectory)
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_discrete_collision_is_not_waived(self):
        result = retract_evidence_from_trajectory(fake_trajectory(
            status="DISCRETE_SAMPLES_REJECTED",
            states=(sample(0, -7), sample(.5, 4, collision=True)),
        ))
        self.assertEqual(result["status"], "COLLISION")
        self.assertEqual(result["max_punch_overlap_mm3"], 1.25)

    def test_mismatched_sampled_endpoint_invalidates_clearance(self):
        result = retract_evidence_from_trajectory(fake_trajectory(
            states=(sample(0, -7), sample(1, 40.0)),
        ))
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertIn("does not match", result["reason"])

    def test_conditional_end_zone_does_not_become_green(self):
        trajectory = fake_trajectory(
            status="SAMPLED_INTERVAL_CONDITIONAL_END_ZONE",
        )
        # Native D6 marks an accepted end-zone overlap is_safe=False:
        # it must stay conditional rather than become COLLISION or PASS.
        phase = trajectory.phase_results[0]
        phase.is_safe = False
        conditional_state = sample(.5, 4.0, collision=True)
        conditional_state.is_safe = False
        conditional_state.is_conditional_safe = True
        phase.states = (
            sample(0, -7), conditional_state, sample(1, 15),
        )
        result = retract_evidence_from_trajectory(trajectory)
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertTrue(result["end_position_sampled"])

    def test_non_conditional_die_penetration_stays_collision(self):
        trajectory = fake_trajectory(
            status="DISCRETE_SAMPLES_REJECTED",
            states=(sample(0, -7), sample(.5, 5, collision=True)),
        )
        trajectory.phase_results[0].is_safe = False
        result = retract_evidence_from_trajectory(trajectory)
        self.assertEqual(result["status"], "COLLISION")

    def test_gui_passes_clearance_and_shows_retract_per_bend(self):
        root = Path(__file__).resolve().parents[1]
        gui = (root / "freecad_smbs/commands/pressbrake_2d.py"
               ).read_text(encoding="utf-8")
        self.assertIn('"Status", "RETRACT"', gui)
        self.assertIn('initial_clearance_mm=(', gui)
        self.assertIn('self.cycle_config.initial_clearance_mm', gui)
        self.assertIn('self._manual_retract_verdicts[step["bend_id"]]', gui)
        self.assertIn('retract.get("end_z_mm")', gui)

    def test_native_trajectory_uses_shared_ram_geometry_for_both_phases(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "smbs_core/kinematics2d/motion_trajectory_3d.py"
                  ).read_text(encoding="utf-8")
        self.assertIn('ram_cycle_positions(', source)
        self.assertIn('ram_cycle["retract_end_z_mm"]', source)
        self.assertIn('ram_cycle["approach_start_z_mm"]', source)
        self.assertNotIn('40.0 - bottom_ram_z', source)
        self.assertNotIn('50.0 + u * (coupled.contact_z_mm - 50.0)', source)
        ast.parse(source)


if __name__ == "__main__":
    unittest.main()
