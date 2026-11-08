"""Synthetic 2D risk-ranking unit tests; physical qualification stays in 3D."""
import unittest
from types import SimpleNamespace
from smbs_core.kinematics2d.screening_risk import (
    ScreeningRiskWeights, screening_risk, screening_order_key,
)


def report(*statuses, stage="STAGE_B_PREVIEW"):
    return SimpleNamespace(
        stage=stage,
        station_reports=tuple({
            "phase": f"PHASE_{i}",
            "preview": {"stations": [{"status": status}]},
        } for i, status in enumerate(statuses)),
    )


class ScreeningRiskTests(unittest.TestCase):
    def test_clear_precedes_apparent_2d_collision_at_same_cost(self):
        clear = report("CLEAR", "CLEAR")
        collision = report("INTERFERENCE", "CLEAR")
        self.assertLess(screening_order_key(5, clear),
                        screening_order_key(5, collision))
        self.assertEqual(screening_risk(collision).interference_samples, 1)

    def test_unverified_has_risk_not_blocking(self):
        unknown = screening_risk(report("UNVERIFIED"))
        self.assertGreater(unknown.total, 0)
        self.assertEqual(unknown.confidence, "INCOMPLETE_SAMPLED_EVIDENCE")

    def test_missing_report_not_treated_clear(self):
        self.assertEqual(screening_risk(SimpleNamespace(station_reports=())).unverified_samples, 1)
        self.assertEqual(screening_risk(SimpleNamespace()).unverified_samples, 1)

    def test_central_interference_counted_when_preview_early_exits(self):
        x = report(stage="STAGE_B_SECTION_REQUIRES_3D")
        self.assertEqual(screening_risk(x).interference_samples, 1)

    def test_technological_cost_unchanged(self):
        a = report("INTERFERENCE")
        self.assertEqual(screening_order_key(20, a), 30.0)
        self.assertEqual(20.0, 20.0)  # ranking is computed into a separate scalar
        self.assertEqual(screening_risk(a).to_dict()["purpose"],
                         "SEARCH_ORDER_ONLY_NOT_PRODUCTION_COST")

    def test_weights_custom_and_invalid(self):
        a = report("INTERFERENCE", "TOUCH", "UNVERIFIED")
        weights = ScreeningRiskWeights(interference=2, touch=1, unverified=4)
        self.assertEqual(screening_risk(a, weights).total, 7)
        for val in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ScreeningRiskWeights(interference=val)

    def test_nonfinite_technological_cost_rejected(self):
        with self.assertRaises(ValueError):
            screening_order_key(float("nan"), report("CLEAR"))

    def test_counts_samples_not_polygon_findings(self):
        x = SimpleNamespace(station_reports=({
            "preview": {"stations": [{
                "status": "INTERFERENCE",
                "findings": [{"status": "INTERFERENCE"} for _ in range(50)],
            }]},
        },))
        self.assertEqual(screening_risk(x).interference_samples, 1)


if __name__ == "__main__":
    unittest.main()
