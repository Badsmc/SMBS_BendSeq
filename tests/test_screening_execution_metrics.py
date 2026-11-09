"""Execution evidence records actual sampled stations, not option flags."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.screening_execution_metrics import (
    count_station_screenings, record_station_preview,
)


def preview(*statuses):
    stations = []
    for status in statuses:
        stations.append(SimpleNamespace(
            status=status,
            reason=("sampled_2d_section_not_3d_validated"
                    if status != "UNVERIFIED" else "outside_tool_span"),
        ))
    return SimpleNamespace(status="UNVERIFIED_NEEDS_3D", stations=tuple(stations))


class ScreeningExecutionMetricsTests(unittest.TestCase):
    def test_flags_without_preview_cannot_be_called_execution(self):
        with count_station_screenings() as c:
            pass
        self.assertEqual(c.to_dict()["evaluated_sections"], 0)

    def test_actual_stations_and_unverified_are_distinct(self):
        with count_station_screenings() as c:
            record_station_preview(preview("CLEAR", "INTERFERENCE", "UNVERIFIED"))
        self.assertEqual(c.previews, 1)
        self.assertEqual(c.station_samples, 3)
        self.assertEqual(c.evaluated_sections, 2)
        self.assertEqual(c.unverified_stations, 1)
        self.assertEqual(c.interference_stations, 1)

    def test_nested_scopes_isolated(self):
        with count_station_screenings() as outer:
            record_station_preview(preview("CLEAR"))
            with count_station_screenings() as inner:
                record_station_preview(preview("UNVERIFIED"))
        self.assertEqual(outer.evaluated_sections, 1)
        self.assertEqual(outer.unverified_stations, 0)
        self.assertEqual(inner.unverified_stations, 1)

    def test_disabled_observation_does_not_change_result(self):
        p = preview("INTERFERENCE")
        self.assertIsNone(record_station_preview(p))
        self.assertEqual(p.stations[0].status, "INTERFERENCE")


if __name__ == "__main__":
    unittest.main()
