"""Side-by-side search experiment summary without fabricated OCCT measurements.

SequenceSearchReport.stage_c_native_evaluated counts native validation
*attempts*, not calls to OpenCASCADE boolean kernels. Those calls require
independent instrumentation of the collision backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Optional


def _nonnegative(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _seconds(value: Any, label: str) -> Optional[float]:
    if value is None:
        return None
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class SearchMeasurement:
    mode: str
    status: str
    found_sequence: tuple[str, ...] | None
    elapsed_s: float
    time_to_first_full_candidate_s: float | None
    time_to_first_verified_plan_s: float | None
    stage_b_evaluated: int
    stage_c_validation_attempts: int
    expensive_step_evaluations: int
    occt_boolean_calls: int | None = None
    occt_boolean_elapsed_s: float | None = None
    model_id: str = ""
    tooling_setup_id: str = ""
    placement_id: str = ""
    tolerance_signature: str = ""

    def __post_init__(self) -> None:
        if self.mode not in ("BASELINE", "2D_RISK"):
            raise ValueError("mode must be BASELINE or 2D_RISK")
        for field in ("elapsed_s", "time_to_first_full_candidate_s",
                      "time_to_first_verified_plan_s", "occt_boolean_elapsed_s"):
            _seconds(getattr(self, field), field)
        for field in ("stage_b_evaluated", "stage_c_validation_attempts",
                      "expensive_step_evaluations"):
            _nonnegative(getattr(self, field), field)
        if self.occt_boolean_calls is not None:
            _nonnegative(self.occt_boolean_calls, "occt_boolean_calls")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "status": self.status,
            "found_sequence": list(self.found_sequence) if self.found_sequence is not None else None,
            "elapsed_s": self.elapsed_s,
            "time_to_first_full_candidate_s": self.time_to_first_full_candidate_s,
            "time_to_first_verified_plan_s": self.time_to_first_verified_plan_s,
            "stage_b_evaluated": self.stage_b_evaluated,
            "stage_c_validation_attempts": self.stage_c_validation_attempts,
            "expensive_step_evaluations": self.expensive_step_evaluations,
            "occt_boolean_calls": self.occt_boolean_calls,
            "occt_boolean_elapsed_s": self.occt_boolean_elapsed_s,
            "model_id": self.model_id, "tooling_setup_id": self.tooling_setup_id,
            "placement_id": self.placement_id,
            "tolerance_signature": self.tolerance_signature,
        }


def from_search_report(
    report: Any, mode: str, *,
    occt_boolean_calls: int | None = None,
    occt_boolean_elapsed_s: float | None = None,
    model_id: str = "",
    tooling_setup_id: str = "",
    placement_id: str = "",
    tolerance_signature: str = "",
) -> SearchMeasurement:
    """Capture actual report counters, never estimate OCCT from Stage C."""
    sequence = getattr(report, "found_sequence", None)
    return SearchMeasurement(
        mode=mode,
        status=str(getattr(getattr(report, "status", ""), "value", getattr(report, "status", ""))),
        found_sequence=tuple(sequence) if sequence is not None else None,
        elapsed_s=float(report.elapsed_s),
        time_to_first_full_candidate_s=getattr(report, "time_to_first_full_candidate_s", None),
        time_to_first_verified_plan_s=getattr(report, "time_to_first_verified_plan_s", None),
        stage_b_evaluated=int(report.stage_b_evaluated),
        stage_c_validation_attempts=int(report.stage_c_native_evaluated),
        expensive_step_evaluations=int(report.step_evaluations),
        occt_boolean_calls=occt_boolean_calls,
        occt_boolean_elapsed_s=occt_boolean_elapsed_s,
        model_id=model_id,
        tooling_setup_id=tooling_setup_id,
        placement_id=placement_id,
        tolerance_signature=tolerance_signature,
    )


def compare_measurements(
    baseline: SearchMeasurement, screened: SearchMeasurement
) -> dict[str, Any]:
    """Explicitly refuse to compare incompatible machine/part configurations."""
    if baseline.mode != "BASELINE" or screened.mode != "2D_RISK":
        raise ValueError("expected BASELINE vs 2D_RISK")
    for key in ("model_id", "tooling_setup_id", "placement_id", "tolerance_signature"):
        left, right = getattr(baseline, key), getattr(screened, key)
        if not left or left != right:
            raise ValueError(f"incompatible or unspecified comparison configuration: {key}")
    def delta(attr: str):
        a, b = getattr(baseline, attr), getattr(screened, attr)
        return None if a is None or b is None else a - b

    return {
        "schema_version": 1,
        "comparison_status": "OBSERVED_MEASUREMENTS_NOT_PHYSICAL_CERTIFICATION",
        "baseline": baseline.to_dict(),
        "screened": screened.to_dict(),
        "savings": {
            "stage_c_validation_attempts": delta("stage_c_validation_attempts"),
            "expensive_step_evaluations": delta("expensive_step_evaluations"),
            "occt_boolean_calls": delta("occt_boolean_calls"),
            "occt_boolean_elapsed_s": delta("occt_boolean_elapsed_s"),
            "total_elapsed_s": delta("elapsed_s"),
            "time_to_first_verified_plan_s": delta("time_to_first_verified_plan_s"),
        },
        "caveat": (
            "Stage C attempts and step evaluations are NOT actual OCCT boolean "
            "calls. Missing OCCT instrumentation or absent verified plan yields null."
        ),
    }
