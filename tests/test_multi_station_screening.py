"""Multi-station preview safety contracts; no native OCCT calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactFinding, ContactReport, ContactStatus,
)
from smbs_core.kinematics2d.multi_station_screening import screen_machine_stations_2d


class MultiStationTests(unittest.TestCase):
    def _run(self, xs, *, punch=(-100, 100), die=(-100, 100), status=ContactStatus.CLEAR):
        report = ContactReport(status, (
            ContactFinding("PUNCH", "flange", status, "synthetic_test"),), 0.02)
        with patch(
            "smbs_core.kinematics2d.multi_station_screening.compute_machine_cutting_plane_section",
            side_effect=lambda *args, **kwargs: {"cutting_plane_xm": kwargs["target_xm"]},
        ) as section_mock, patch(
            "smbs_core.kinematics2d.multi_station_screening.check_sheet_tooling_preview",
            return_value=report,
        ) as oracle_mock:
            result = screen_machine_stations_2d(
                object(), {}, object(), "B01", object(), xs,
                verified_punch_span_xm=punch, verified_die_span_xm=die,
            )
            return result, section_mock.call_args_list, oracle_mock.call_count

    def test_every_station_uses_matching_machine_x(self):
        result, calls, count = self._run((-25, 0, 25))
        self.assertEqual([c.kwargs["target_xm"] for c in calls], [-25, 0, 25])
        self.assertEqual(count, 3)
        self.assertEqual(result.status, "SAMPLED_SECTIONS_CLEAR_OR_TOUCH_3D_REQUIRED")

    def test_sampled_2d_collision_is_not_proven_blocking(self):
        result, _, _ = self._run((0,), status=ContactStatus.INTERFERENCE)
        self.assertEqual(result.status, "2D_INTERFERENCE_REQUIRES_3D")
        self.assertEqual(result.stations[0].findings[0]["sheet_component"], "flange")

    def test_missing_tool_spans_must_not_call_oracle(self):
        result, calls, count = self._run((0,), punch=None)
        self.assertEqual(result.status, "UNVERIFIED_NEEDS_3D")
        self.assertEqual(count, 0)
        self.assertEqual(len(calls), 0)

    def test_tool_end_cannot_be_extrapolated(self):
        result, calls, count = self._run((-90, 0, 90), punch=(-50, 50))
        self.assertEqual([s.status for s in result.stations],
                         ["UNVERIFIED", "CLEAR", "UNVERIFIED"])
        self.assertEqual(count, 1)
        self.assertEqual(len(calls), 1)

    def test_fail_safe_on_section_error(self):
        with patch(
            "smbs_core.kinematics2d.multi_station_screening.compute_machine_cutting_plane_section",
            side_effect=RuntimeError("no section"),
        ):
            result = screen_machine_stations_2d(
                object(), {}, object(), "B01", object(), (0,),
                verified_punch_span_xm=(-10, 10), verified_die_span_xm=(-10, 10),
            )
        self.assertEqual(result.status, "UNVERIFIED_NEEDS_3D")
        self.assertIn("no section", result.stations[0].reason)

    def test_invalid_stations_rejected(self):
        with self.assertRaises(ValueError):
            screen_machine_stations_2d(object(), {}, object(), "B01", object(), ())
        with self.assertRaises(ValueError):
            screen_machine_stations_2d(object(), {}, object(), "B01", object(), (float("nan"),))

    def test_report_serializes_all_stations(self):
        result, _, _ = self._run((0, 1))
        exported = result.to_dict()
        self.assertEqual(len(exported["stations"]), 2)
        self.assertEqual(exported["evidence"], "SAMPLED_2D_ONLY_NOT_3D_PROOF")


if __name__ == "__main__":
    unittest.main()
