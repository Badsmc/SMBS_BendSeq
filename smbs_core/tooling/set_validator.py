"""
set_validator.py - Validation of ToolingSet against canonical part and bends (Requirement 3.1).

Validates:
- Punch tip radius vs bend radius
- Punch/Die tip angle vs bend angle
- Die V-width vs sheet thickness
- Clamp positions configuration
- Punch changeover necessity (Requirement Q10)

Pure Python. No FreeCAD, no OCC dependencies. Non-blocking validation:
returns ToolingSetValidationReport with structured compatible/incompatible lists
and changeover proposal steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from smbs_core.geometry_backend.io.tooling_catalog import ToolDescriptor
from smbs_core.geometry_backend.io.tooling_set import ToolingSet


@dataclass
class ToolingSetValidationReport:
    """Detailed validation report for applying a ToolingSet to a sheet metal part."""
    set_id: str
    is_valid: bool
    fully_compatible: bool
    compatible_bends: List[str] = field(default_factory=list)
    incompatible_bends: Dict[str, List[str]] = field(default_factory=dict)
    requires_changeover: bool = False
    proposed_changeover_steps: List[int] = field(default_factory=list)
    clamp_validation: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to JSON-serializable dictionary matching schema."""
        return {
            "set_id": self.set_id,
            "is_valid": self.is_valid,
            "fully_compatible": self.fully_compatible,
            "compatible_bends": list(self.compatible_bends),
            "incompatible_bends": {k: list(v) for k, v in self.incompatible_bends.items()},
            "requires_changeover": self.requires_changeover,
            "proposed_changeover_steps": list(self.proposed_changeover_steps),
            "clamp_validation": self.clamp_validation,
            "warnings": list(self.warnings),
        }


def _extract_numeric_value(val: Any) -> Optional[float]:
    """Helper to safely extract float from float, int, or {'value': float} dict."""
    if val is None:
        return None
    if isinstance(val, dict):
        v = val.get("value")
        return float(v) if v is not None else None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def validate_tooling_set(
    tooling_set: ToolingSet,
    canonical_part: Optional[Dict[str, Any]] = None,
    bends_map: Optional[Dict[str, Any]] = None,
    thickness_mm: Optional[float] = None,
    punch_desc: Optional[ToolDescriptor] = None,
    die_desc: Optional[ToolDescriptor] = None,
    candidate_sequence: Optional[Sequence[Dict[str, Any]]] = None,
) -> ToolingSetValidationReport:
    """
    Validate a ToolingSet against part bends and geometry (Requirement 3.1).

    Non-blocking: evaluates all bends and returns a structured report.
    """
    # Resolve bends_map and thickness_mm from canonical_part if provided
    if canonical_part is not None:
        payload = canonical_part.get("payload", canonical_part)
        if bends_map is None:
            bends_map = payload.get("bends", {})
        if thickness_mm is None:
            raw_t = payload.get("thickness_mm")
            thickness_mm = _extract_numeric_value(raw_t)

    bends_map = bends_map or {}
    compatible_bends: List[str] = []
    incompatible_bends: Dict[str, List[str]] = {}
    warnings: List[str] = []

    # Extract punch metadata if descriptor provided
    punch_meta = punch_desc.metadata if punch_desc is not None else {}
    punch_radius = _extract_numeric_value(punch_meta.get("radius_mm"))
    punch_angle = _extract_numeric_value(punch_meta.get("angle_deg"))

    # Extract die metadata if descriptor provided
    die_meta = die_desc.metadata if die_desc is not None else {}
    die_v = _extract_numeric_value(die_meta.get("v_width_mm"))
    die_angle = _extract_numeric_value(die_meta.get("angle_deg"))

    for bend_id, bend_data in bends_map.items():
        reasons: List[str] = []

        bend_angle = _extract_numeric_value(bend_data.get("angle_deg"))
        bend_radius = _extract_numeric_value(bend_data.get("radius_mm"))
        bend_length = _extract_numeric_value(bend_data.get("length_mm"))

        # 1. Tip radius check: punch radius cannot be much larger than bend internal radius
        if punch_radius is not None and bend_radius is not None and bend_radius > 0:
            if punch_radius > bend_radius * 1.2:
                reasons.append(
                    f"punch tip radius ({punch_radius:.2f}mm) exceeds bend radius ({bend_radius:.2f}mm)"
                )

        # 2. Angle checks: tool tip angle must be <= bend angle (to avoid bottoming)
        if bend_angle is not None:
            if punch_angle is not None and punch_angle > bend_angle + 1.0:
                reasons.append(
                    f"punch angle ({punch_angle:.1f}°) cannot form sharper bend ({bend_angle:.1f}°)"
                )
            if die_angle is not None and die_angle > bend_angle + 1.0:
                reasons.append(
                    f"die angle ({die_angle:.1f}°) cannot form sharper bend ({bend_angle:.1f}°)"
                )

        # 3. Die V-width check relative to sheet thickness
        if die_v is not None and thickness_mm is not None and thickness_mm > 0:
            if die_v < 3.5 * thickness_mm:
                reasons.append(
                    f"die V-width ({die_v:.1f}mm) too narrow for sheet thickness ({thickness_mm:.2f}mm)"
                )
            elif die_v > 15.0 * thickness_mm:
                reasons.append(
                    f"die V-width ({die_v:.1f}mm) excessively wide for sheet thickness ({thickness_mm:.2f}mm)"
                )

        if reasons:
            incompatible_bends[bend_id] = reasons
        else:
            compatible_bends.append(bend_id)

    # Validate clamps if defined in tooling_set
    clamp_validation: Optional[Dict[str, Any]] = None
    if tooling_set.clamp_positions_mm:
        clamp_warnings: List[str] = []
        positions = list(tooling_set.clamp_positions_mm)
        if len(positions) < 2:
            clamp_warnings.append(
                f"ToolingSet '{tooling_set.set_id}' defines {len(positions)} clamp position(s); "
                f"minimum 2 recommended under punch chain"
            )
        clamp_validation = {
            "valid": len(positions) >= 2,
            "positions_mm": positions,
            "warnings": clamp_warnings,
        }
        if clamp_warnings:
            warnings.extend(clamp_warnings)

    # Changeover analysis (Requirement Q10)
    requires_changeover = bool(incompatible_bends and compatible_bends)
    proposed_changeover_steps: List[int] = []

    if candidate_sequence:
        for step_idx, step in enumerate(candidate_sequence, start=1):
            bid = step.get("bend_id")
            if bid in incompatible_bends:
                proposed_changeover_steps.append(step_idx)

    # Set overall validity: valid if part has no bends or at least one bend compatible
    if not bends_map:
        is_valid = True
        fully_compatible = True
    else:
        fully_compatible = len(incompatible_bends) == 0
        is_valid = len(compatible_bends) > 0

    if not fully_compatible and incompatible_bends:
        warnings.append(
            f"ToolingSet '{tooling_set.set_id}' has {len(incompatible_bends)} incompatible bend(s): "
            f"{', '.join(sorted(incompatible_bends.keys()))}"
        )

    return ToolingSetValidationReport(
        set_id=tooling_set.set_id,
        is_valid=is_valid,
        fully_compatible=fully_compatible,
        compatible_bends=compatible_bends,
        incompatible_bends=incompatible_bends,
        requires_changeover=requires_changeover,
        proposed_changeover_steps=proposed_changeover_steps,
        clamp_validation=clamp_validation,
        warnings=warnings,
    )
