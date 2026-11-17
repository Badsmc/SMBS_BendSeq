"""Machine-X layout shared by Coin3D and the native OCC check."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite


SectionSpans = tuple[tuple[float, float], ...]
CONTACT_EPS_MM = 1e-6
MAX_PUNCH_SHORTFALL_MM = 15.0


@dataclass(frozen=True)
class ToolAssembly:
    bend_length_mm: float
    punch_spans_xm: SectionSpans
    die_spans_xm: SectionSpans
    clamp_spans_xm: SectionSpans

    @classmethod
    def from_plan(cls, plan):
        if plan["status"] == "ASSEMBLY_SEARCH_LIMIT":
            raise ValueError("Подбор остановился до проверки всех вариантов раскладки.")
        if plan["status"] != "ASSEMBLY_CANDIDATE_UNVERIFIED":
            raise ValueError("Не удалось подобрать общий набор для самой длинной линии гиба.")
        clamps = plan["clamps"]
        if clamps["proposed_reposition"]:
            raise ValueError("Задайте фактические центры зажимов или их число для симметричного ряда.")

        def spans(kind):
            return normalize_section_spans(
                (item["start_x_mm"], item["end_x_mm"]) for item in plan[kind + "_segments"]
            )

        return cls(
            plan["required_mm"], spans("punch"), spans("die"),
            clamp_spans_for_centres(clamps["centers_mm"], clamps["width_mm"]),
        )


def clamp_spans_for_centres(centres_mm, width_mm) -> SectionSpans:
    half = width_mm / 2.0
    return tuple((centre - half, centre + half) for centre in centres_mm)


def punch_segment_supports(punch_spans, clamp_spans):
    """Clamp numbers and positive axial overlap for every punch segment."""
    supports = []
    for left, right in punch_spans:
        contacts = []
        for number, (clamp_left, clamp_right) in enumerate(clamp_spans, 1):
            overlap = min(right, clamp_right) - max(left, clamp_left)
            if overlap > CONTACT_EPS_MM:
                contacts.append((number, overlap))
        supports.append(tuple(contacts))
    return tuple(supports)


def normalize_section_spans(spans) -> SectionSpans:
    spans = tuple(sorted((float(left), float(right)) for left, right in spans))
    if any(not (isfinite(left) and isfinite(right) and right - left > 1e-3)
           for left, right in spans):
        raise ValueError("Недопустимые границы сегмента оснастки.")
    if any(a[1] > b[0] + 1e-6 for a, b in pairwise(spans)):
        raise ValueError("Сегменты оснастки перекрываются по X.")
    return spans


def native_section_options(tooling, kind: str) -> dict:
    spans = getattr(tooling, f"{kind}_section_spans_xm", ())
    if spans:
        return {"section_spans_xm": spans}
    return {"section_offsets_xm": getattr(tooling, f"{kind}_section_offsets_xm", ()) or None}


def has_native_sections(tooling) -> bool:
    return any(option for kind in ("punch", "die")
               for option in native_section_options(tooling, kind).values())
