"""No fabricated savings; mismatched configurations must not be compared."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.screening_experiment_metrics import (
    SearchMeasurement, compare_measurements, from_search_report,
)


def measurement(mode, **kw):
    values = dict(
        mode=mode, status="NO_FEASIBLE_SEQUENCE", found_sequence=None,
        elapsed_s=12.0 if mode == "BASELINE" else 9.0,
        time_to_first_full_candidate_s=None,
        time_to_first_verified_plan_s=None,
        stage_b_evaluated=10,
        stage_c_validation_attempts=8 if mode == "BASELINE" else 6,
        expensive_step_evaluations=5 if mode == "BASELINE" else 4,
        model_id="Z-(ok)", tooling_setup_id="AP1017+AT3015",
        placement_id="same-pose", tolerance_signature="tol-v1",
    )
    values.update(kw)
    return SearchMeasurement(**values)


class ExperimentMetricTests(unittest.TestCase):
    def test_3d_attempts_are_not_occt_calls(self):
        result = compare_measurements(measurement("BASELINE"), measurement("2D_RISK"))
        self.assertEqual(result["savings"]["stage_c_validation_attempts"], 2)
        self.assertIsNone(result["savings"]["occt_boolean_calls"])
        self.assertIsNone(result["savings"]["time_to_first_verified_plan_s"])

    def test_occt_calls_only_when_explicitly_measured(self):
        result = compare_measurements(
            measurement("BASELINE", occt_boolean_calls=100),
            measurement("2D_RISK", occt_boolean_calls=65),
        )
        self.assertEqual(result["savings"]["occt_boolean_calls"], 35)

    def test_time_to_plan_only_when_both_present(self):
        result = compare_measurements(
            measurement("BASELINE", time_to_first_verified_plan_s=10.0),
            measurement("2D_RISK", time_to_first_verified_plan_s=7.0),
        )
        self.assertEqual(result["savings"]["time_to_first_verified_plan_s"], 3.0)

    def test_reject_configuration_mismatch(self):
        for prop in ("model_id", "tooling_setup_id", "placement_id", "tolerance_signature"):
            with self.subTest(prop=prop), self.assertRaises(ValueError):
                compare_measurements(measurement("BASELINE"),
                                     measurement("2D_RISK", **{prop: "different"}))

    def test_reject_unspecified_configuration(self):
        with self.assertRaises(ValueError):
            compare_measurements(measurement("BASELINE", model_id=""),
                                 measurement("2D_RISK", model_id=""))

    def test_validate_impossible_measurements(self):
        with self.assertRaises(ValueError):
            measurement("BASELINE", stage_c_validation_attempts=-1)
        with self.assertRaises(ValueError):
            measurement("BASELINE", occt_boolean_calls=-1)
        with self.assertRaises(ValueError):
            measurement("BASELINE", elapsed_s=float("nan"))

    def test_report_adapter_does_not_infer_occt(self):
        report = SimpleNamespace(
            status=SimpleNamespace(value="STRICT_VALIDATED"),
            found_sequence=("B01", "B02"), elapsed_s=5.5,
            time_to_first_full_candidate_s=2.0,
            time_to_first_verified_plan_s=4.0,
            stage_b_evaluated=9, stage_c_native_evaluated=7,
            step_evaluations=6,
        )
        result = from_search_report(
            report, "BASELINE", model_id="Z-(ok)",
            tooling_setup_id="AP1017+AT3015",
            placement_id="same-pose", tolerance_signature="tol-v1",
        )
        self.assertEqual(result.stage_c_validation_attempts, 7)
        self.assertIsNone(result.occt_boolean_calls)
        self.assertEqual(result.found_sequence, ("B01", "B02"))


if __name__ == "__main__":
    unittest.main()
