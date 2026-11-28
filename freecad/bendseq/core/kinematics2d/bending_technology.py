"""Preliminary air-bending estimates, explicit units, no inferred ratings.

90 degree air bend reference: F[kN] = 1.42 * Rm[MPa] * t[mm]^2 *
L[mm] / (1000 * V[mm]). Source: HARSLE bending force calculator.
Not applicable to bottoming, coining or proof of machine/tool capacity.
"""
from dataclasses import dataclass, asdict
from math import isfinite
from .flange_die_check import positive_mm

FORMULA_SOURCE = "https://www.harsle.com/bending-force-calculator/"


@dataclass(frozen=True)
class Material:
    name: str
    tensile_mpa: float
    source: str

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name.strip()
                or not isinstance(self.source, str) or not self.source.strip()
                or type(self.tensile_mpa) not in (int, float)
                or not isfinite(self.tensile_mpa) or not 0 < self.tensile_mpa <= 10000):
            raise ValueError("Material requires name, positive tensile strength and source")


@dataclass(frozen=True)
class TechnologyInputs:
    material: Material | None = None
    machine_limit_kn: float | None = None

    def __post_init__(self):
        value = self.machine_limit_kn
        if value is not None and (type(value) not in (int, float) or not isfinite(value) or value <= 0):
            raise ValueError("Invalid machine force limit (kN)")


def estimate_force_kn(tensile_mpa, thickness_mm, length_mm, opening_mm):
    vals = (tensile_mpa, thickness_mm, length_mm, opening_mm)
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in vals):
        raise ValueError("Air bending estimate requires positive finite inputs")
    value = 1.42 * tensile_mpa * thickness_mm**2 * length_mm / (1000 * opening_mm)
    if not isfinite(value):
        raise ValueError("Air bending estimate overflow")
    return value


def _rating(metadata):
    # Legacy maximum_load has no guaranteed units. Do not use it.
    value = metadata.get("max_load_kn_per_m")
    source = metadata.get("load_rating_source")
    if (type(value) not in (int, float) or not isfinite(value) or value <= 0
            or not isinstance(source, str) or not source.strip()):
        return None
    return float(value)


def assess_technology(bends, opening, punch_meta, die_meta, inputs, *, groups=None):
    """Per-bend reference and simultaneous-load checks for the actual stroke groups.

    Only explicit 90-degree bend groups can be filtered on estimated overload.
    Other angles have a reference estimate but no force verdict. No clear result
    certifies actual tonnage, concentrated load, off-center load, or mounting.
    """
    bends = tuple(bends)
    groups = tuple(tuple(g) for g in groups) if groups is not None else tuple((b.bend_id,) for b in bends)
    by_id = {b.bend_id: b for b in bends}
    ratings = {"punch": _rating(punch_meta), "die": _rating(die_meta)}
    warnings = ["TECHNOLOGY_ESTIMATE_ONLY", "MOUNTING_AND_LOCAL_LOAD_UNVERIFIED"]
    if inputs.material is None:
        warnings.append("MATERIAL_UNKNOWN")
    if opening is None:
        warnings.append("V_OPENING_UNKNOWN")
    if inputs.machine_limit_kn is None:
        warnings.append("MACHINE_FORCE_LIMIT_UNKNOWN")
    for kind, rating in ratings.items():
        if rating is None:
            warnings.append(kind.upper() + "_LOAD_RATING_UNKNOWN")
    rows, blocked = [], []
    for group in groups:
        members = []
        for bid in group:
            bend = by_id[bid]
            thickness = positive_mm(bend.thickness_mm)
            angle = bend.angle_deg
            is_90 = type(angle) in (int, float) and isfinite(angle) and abs(abs(angle)-90) <= 1e-6
            force = None
            if thickness is None:
                warnings.append("THICKNESS_UNKNOWN: " + bid)
            if not is_90:
                warnings.append("NON_90_DEGREE_REFERENCE_ONLY: " + bid)
            if inputs.material is not None and opening is not None and thickness is not None:
                force = estimate_force_kn(inputs.material.tensile_mpa, thickness, bend.length_mm, opening)
            per_m = force * 1000 / bend.length_mm if force is not None else None
            members.append(dict(bend_id=bid, length_mm=bend.length_mm, thickness_mm=thickness,
                                angle_deg=angle, reference_90deg_kn=force, reference_kn_per_m=per_m))
            for kind, rating in ratings.items():
                if is_90 and per_m is not None and rating is not None and per_m > rating:
                    blocked.append(f"ESTIMATED_{kind.upper()}_OVERLOAD: {bid}")
        total = (sum(m["reference_90deg_kn"] for m in members)
                 if all(m["reference_90deg_kn"] is not None for m in members) else None)
        actual_90 = all(type(m["angle_deg"]) in (int, float) and
                        abs(abs(m["angle_deg"])-90) <= 1e-6 for m in members)
        if actual_90 and total is not None and inputs.machine_limit_kn is not None and total > inputs.machine_limit_kn:
            blocked.append("ESTIMATED_MACHINE_OVERLOAD: " + ",".join(group))
        rows.append(dict(bends=members, total_reference_kn=total, angle_scope="90_DEGREE" if actual_90 else "REFERENCE_ONLY"))
    return dict(status="ESTIMATED_LIMIT_EXCEEDED" if blocked else "UNVERIFIED_ESTIMATE",
                warnings=list(dict.fromkeys(warnings)), blocked=list(dict.fromkeys(blocked)),
                material=asdict(inputs.material) if inputs.material else None,
                machine_limit_kn=inputs.machine_limit_kn, load_ratings_kn_per_m=ratings,
                load_rating_sources={k: m.get("load_rating_source") for k,m in
                                     (("punch",punch_meta),("die",die_meta))},
                opening_mm=opening, strokes=rows, formula_source=FORMULA_SOURCE)
