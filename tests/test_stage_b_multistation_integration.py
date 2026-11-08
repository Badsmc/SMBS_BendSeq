"""Synthetic integration contracts for opt-in Stage B multi-station previews.

These tests do not validate real CAD contact or native OCCT.
"""
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.contact_oracle_2d import ContactReport, ContactStatus
from smbs_core.kinematics2d.preliminary_screening import (
    ScreeningDecision, screen_candidate_stage_b,
)
from smbs_core.kinematics2d.sequence_search import (
    search_bend_sequence_25d, search_bend_sequence_backward_25d,
)


class DummyFold:
    def __init__(self):
        self.angles = {"B": 0.0}
        self.transforms = {"P": object()}
        self.bends = {"B": SimpleNamespace(
            axis_xyz=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
            bend_zone=SimpleNamespace(outer_radius_mm=2.0),
            angle_deg=90.0,
        )}

    def set_angle(self, bid, angle):
        self.angles[bid] = angle


class DummyTooling:
    def __init__(self):
        self.aligned = False
        self.match_active_bend_length = False

    def align_to_cross_section(self, *args, **kwargs):
        self.aligned = True

    def set_punch_ram_z(self, *args, **kwargs):
        pass


class StageBIntegrationTests(unittest.TestCase):
    def make_patches(self, station_status="SAMPLED_SECTIONS_CLEAR_OR_TOUCH_3D_REQUIRED"):
        coupled = SimpleNamespace(
            tooling_status="VERIFIED", contact_z_mm=5.0,
            ram_z_for_angle=lambda x: 5.0 - x / 90,
            stroke_for_angle=lambda x: x / 90,
        )
        station = SimpleNamespace(to_dict=lambda: {"status": station_status})
        plan = SimpleNamespace(stations_xm=(-10.0, 0.0, 10.0),
                               to_dict=lambda: {"stations_xm": [-10.0, 0.0, 10.0]})
        return (
            patch("smbs_core.kinematics2d.preliminary_screening.WorkpiecePlacement3D.compute_placement",
                  return_value=object()),
            patch("smbs_core.kinematics2d.preliminary_screening.compute_machine_cutting_plane_section",
                  return_value={}),
            patch("smbs_core.kinematics2d.preliminary_screening.CoupledAirBendingKinematics.from_tooling_and_fold_state",
                  return_value=coupled),
            patch("smbs_core.kinematics2d.preliminary_screening.check_sheet_tooling_preview",
                  return_value=ContactReport(ContactStatus.CLEAR, (), 0.02)),
            patch("smbs_core.kinematics2d.preliminary_screening.plan_machine_stations",
                  return_value=plan),
            patch("smbs_core.kinematics2d.preliminary_screening.screen_machine_stations_2d",
                  return_value=station),
        )

    def test_stage_b_opt_in_collects_phase_reports_and_restores_state(self):
        fold, tool = DummyFold(), DummyTooling()
        original = dict(fold.angles)
        original_transforms = dict(fold.transforms)
        patches = self.make_patches()
        entered = [p.__enter__() for p in patches]
        try:
            result = screen_candidate_stage_b(
                fold, "B", tool, {"P": [(0, 0), (1, 0), (1, 1)]},
                multi_station_enabled=True,
                verified_punch_span_xm=(-50, 50),
                verified_die_span_xm=(-50, 50),
                fractions=(0, 1),
            )
            self.assertEqual(result.decision, ScreeningDecision.CONTINUE_TO_3D)
            self.assertEqual(len(result.station_reports), 5)
            self.assertEqual(entered[5].call_count, 5)
            self.assertIn("station_reports", result.to_dict())
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)
        self.assertEqual(fold.angles, original)
        self.assertEqual(fold.transforms, original_transforms)
        self.assertFalse(tool.aligned)

    def test_interference_still_requires_3d(self):
        fold, tool = DummyFold(), DummyTooling()
        patches = self.make_patches("2D_INTERFERENCE_REQUIRES_3D")
        for p in patches:
            p.__enter__()
        try:
            result = screen_candidate_stage_b(
                fold, "B", tool, {"P": [(0, 0), (1, 0), (1, 1)]},
                multi_station_enabled=True, fractions=(0,),
            )
            self.assertFalse(result.is_blocking)
            self.assertEqual(result.station_reports[0]["preview"]["status"],
                             "2D_INTERFERENCE_REQUIRES_3D")
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)

    def test_failed_station_geometry_is_unverified_not_blocking(self):
        fold, tool = DummyFold(), DummyTooling()
        patches = self.make_patches()
        for p in patches:
            p.__enter__()
        try:
            with patch("smbs_core.kinematics2d.preliminary_screening.plan_machine_stations",
                       side_effect=ValueError("bad station")):
                result = screen_candidate_stage_b(
                    fold, "B", tool, {"P": [(0, 0), (1, 0), (1, 1)]},
                    multi_station_enabled=True, fractions=(0,),
                )
            self.assertFalse(result.is_blocking)
            self.assertEqual(result.station_reports[0]["preview"]["status"],
                             "UNVERIFIED_NEEDS_3D")
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)

    def test_dfs_experiment_switch_on_both_directions(self):
        for search in (search_bend_sequence_25d, search_bend_sequence_backward_25d):
            parameters = inspect.signature(search).parameters
            self.assertFalse(parameters["experimental_multi_station_2d"].default)
            self.assertIn("verified_punch_span_xm", parameters)
            self.assertIn("verified_die_span_xm", parameters)


if __name__ == "__main__":
    unittest.main()
