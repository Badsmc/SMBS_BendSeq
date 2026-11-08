"""Ordering-only 2D screening risk. Never used for physical pruning or cost bounds.

A sampled 2D overlap is a risk signal, NOT an authoritative 3D collision.
The ordering score must never replace the technological transition cost used
for objective totals, lower bounds, pruning or reporting.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class ScreeningRiskWeights:
    interference: float = 10.0
    unverified: float = 3.0
    touch: float = 1.0

    def __post_init__(self) -> None:
        for key in ("interference", "unverified", "touch"):
            value = float(getattr(self, key))
            if not isfinite(value) or value < 0:
                raise ValueError(f"screening risk weight {key} must be finite and >= 0")


@dataclass(frozen=True)
class ScreeningRisk:
    interference_samples: int
    unverified_samples: int
    touch_samples: int
    total: float
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "interference_samples": self.interference_samples,
            "unverified_samples": self.unverified_samples,
            "touch_samples": self.touch_samples,
            "total": self.total,
            "confidence": self.confidence,
            "purpose": "SEARCH_ORDER_ONLY_NOT_PRODUCTION_COST",
        }


def screening_risk(report: Any, weights: ScreeningRiskWeights | None = None) -> ScreeningRisk:
    """Compute deterministic risk from returned station previews.

    Missing data is uncertain, never CLEAR. One sample corresponds to one
    (phase, station) pair; counts avoid duplicating per-polygon findings.
    """
    w = weights if weights is not None else ScreeningRiskWeights()
    reports = getattr(report, "station_reports", ()) or ()
    interference = uncertain = touch = 0
    if not reports:
        uncertain = 1
    for phase in reports:
        preview = phase.get("preview") if isinstance(phase, dict) else None
        if not isinstance(preview, dict):
            uncertain += 1
            continue
        samples = preview.get("stations")
        if not isinstance(samples, (list, tuple)) or not samples:
            # e.g. station planning failed, or verified tooling spans are missing
            uncertain += 1
            continue
        for sample in samples:
            status = sample.get("status", "UNVERIFIED") if isinstance(sample, dict) else "UNVERIFIED"
            if status == "INTERFERENCE":
                interference += 1
            elif status == "TOUCH":
                touch += 1
            elif status != "CLEAR":
                uncertain += 1
    # The central section may have detected interference before all stations
    # were evaluated; do not score it as clear.
    if str(getattr(report, "stage", "")) == "STAGE_B_SECTION_REQUIRES_3D":
        interference += 1
    total = (interference * w.interference
             + uncertain * w.unverified + touch * w.touch)
    confidence = "RISK_ONLY" if not uncertain else "INCOMPLETE_SAMPLED_EVIDENCE"
    return ScreeningRisk(interference, uncertain, touch, total, confidence)


def screening_order_key(
    technological_cost: float,
    report: Any,
    weights: ScreeningRiskWeights | None = None,
) -> float:
    """Ranking score only: preserve untouched technological cost elsewhere."""
    cost = float(technological_cost)
    if not isfinite(cost):
        raise ValueError("nonfinite technological cost")
    return cost + screening_risk(report, weights).total
