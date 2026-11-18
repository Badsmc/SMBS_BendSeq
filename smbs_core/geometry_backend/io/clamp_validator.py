"""
clamp_validator.py - Punch chain validation against clamp constraints (Phase B, S.24a).

Standalone module. Does NOT integrate with pipeline. Caller provides
PunchSegment list + ClampConfig, gets ValidationResult.

Rules (from ROADMAP §11 Q2 spec, 2026-10-18):
  1. At least 2 clamp blocks under the punch chain.
  2. All punch-to-punch joints must fall inside a clamp zone [center - W/2, center + W/2].
  3. No gaps between punches: end_i == start_{i+1} (with tolerance).
  4. End punches may overhang by <= overhang_ratio * punch_length.
  5. Punch total length = bend line length - 10..15mm (selection rule, caller's responsibility).

Pure Python. No OCC, no FreeCAD.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional, List, Tuple

DEFAULT_CLAMP_WIDTH_MM = 150.0
DEFAULT_GAP_BETWEEN_MM = 50.0
DEFAULT_OVERHANG_RATIO = 0.30
DEFAULT_JOINT_TOLERANCE_MM = 0.1
DEFAULT_MIN_CLAMPS_UNDER_PUNCH = 2


class ClampValidatorError(Exception):
    """Raised on invalid input (negative widths, empty chain, malformed schema, etc.)."""


@dataclass(frozen=True)
class ClampConfig:
    width_mm: float                    # W (block width)
    gap_between_mm: float              # gap between blocks
    positions_mm: tuple[float, ...]    # block centers (sorted ascending)
    overhang_ratio: float              # 30% default
    joint_tolerance_mm: float          # 0.1mm default
    min_clamps_under_punch: int        # 2 default
    repositionable: bool = True
    joints_must_be_on_clamps: bool = True
    no_gaps_between_punches: bool = True
    clamp_count: int = 0              # centred row, used when no explicit centres are entered

    height_mm: float = 25.0
    depth_mm: float = 50.0

    def __post_init__(self):
        def finite_number(value):
            return type(value) in (int, float) and isfinite(value)

        if not all(map(finite_number, (self.width_mm, self.gap_between_mm,
                                      self.overhang_ratio, self.joint_tolerance_mm,
                                      self.height_mm, self.depth_mm))):
            raise ClampValidatorError("Clamp dimensions and tolerances must be finite numbers")
        if self.height_mm <= 0 or self.depth_mm <= 0:
            raise ClampValidatorError("Высота и глубина зажима должны быть положительными")
        if self.width_mm <= 0:
            raise ClampValidatorError(f"width_mm must be positive, got {self.width_mm}")
        if self.gap_between_mm < 0:
            raise ClampValidatorError(f"gap_between_mm cannot be negative, got {self.gap_between_mm}")
        if self.overhang_ratio < 0 or self.overhang_ratio > 1:
            raise ClampValidatorError(f"overhang_ratio must be in [0, 1], got {self.overhang_ratio}")
        if self.joint_tolerance_mm < 0:
            raise ClampValidatorError(f"joint_tolerance_mm cannot be negative, got {self.joint_tolerance_mm}")
        if type(self.min_clamps_under_punch) is not int or self.min_clamps_under_punch < 1:
            raise ClampValidatorError(f"min_clamps_under_punch must be >= 1, got {self.min_clamps_under_punch}")
        if type(self.clamp_count) is not int or self.clamp_count < 0:
            raise ClampValidatorError("clamp_count must be a nonnegative integer")
        if any(type(option) is not bool for option in (self.repositionable,
                self.joints_must_be_on_clamps, self.no_gaps_between_punches)):
            raise ClampValidatorError("Clamp options must be booleans")
        if (not isinstance(self.positions_mm, (list, tuple))
                or not all(map(finite_number, self.positions_mm))
                or len(set(self.positions_mm)) != len(self.positions_mm)):
            raise ClampValidatorError("Clamp centres must be a list of distinct finite numbers")
        sorted_pos = tuple(sorted(self.positions_mm))
        if self.positions_mm != sorted_pos:
            object.__setattr__(self, "positions_mm", sorted_pos)

    @property
    def centres_mm(self) -> tuple[float, ...]:
        if self.positions_mm:
            return self.positions_mm
        pitch = self.width_mm + self.gap_between_mm
        half_span = (self.clamp_count - 1) * pitch / 2.0
        return tuple(i * pitch - half_span for i in range(self.clamp_count))

    @classmethod
    def from_defaults(cls, positions_mm: tuple[float, ...] = ()) -> "ClampConfig":
        """Build from defaults + explicit positions."""
        return cls(
            width_mm=DEFAULT_CLAMP_WIDTH_MM,
            gap_between_mm=DEFAULT_GAP_BETWEEN_MM,
            positions_mm=tuple(sorted(positions_mm)),
            overhang_ratio=DEFAULT_OVERHANG_RATIO,
            joint_tolerance_mm=DEFAULT_JOINT_TOLERANCE_MM,
            min_clamps_under_punch=DEFAULT_MIN_CLAMPS_UNDER_PUNCH,
            repositionable=True,
            joints_must_be_on_clamps=True,
            no_gaps_between_punches=True,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ClampConfig":
        """Build from JSON dict. Strict reading: KeyError/TypeError -> ClampValidatorError."""
        try:
            clamps = data["clamps"]
            punch_chain = data["punch_chain"]

            return cls(
                width_mm=clamps["width_mm"],
                gap_between_mm=clamps["gap_between_mm"],
                positions_mm=clamps["positions_mm"],
                overhang_ratio=punch_chain["end_overhang_ratio"],
                joint_tolerance_mm=punch_chain["joint_tolerance_mm"],
                min_clamps_under_punch=clamps["min_clamps_under_punch"],
                repositionable=clamps["repositionable"],
                joints_must_be_on_clamps=punch_chain["joints_must_be_on_clamps"],
                no_gaps_between_punches=punch_chain["no_gaps_between_punches"],
                clamp_count=clamps.get("count", 0),
                height_mm=clamps.get("height_mm", 25.0),
                depth_mm=clamps.get("depth_mm", 50.0),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ClampValidatorError(f"Invalid ClampConfig dict: {e}") from e

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict matching tooling_library_config schema."""
        return {
            "schema_version": "1.0",
            "clamps": {
                "width_mm": self.width_mm,
                "height_mm": self.height_mm,
                "depth_mm": self.depth_mm,
                "gap_between_mm": self.gap_between_mm,
                "positions_mm": list(self.positions_mm),
                "count": self.clamp_count,
                "min_clamps_under_punch": self.min_clamps_under_punch,
                "repositionable": self.repositionable,
            },
            "punch_chain": {
                "joints_must_be_on_clamps": self.joints_must_be_on_clamps,
                "joint_tolerance_mm": self.joint_tolerance_mm,
                "no_gaps_between_punches": self.no_gaps_between_punches,
                "end_overhang_ratio": self.overhang_ratio,
            },
        }


@dataclass(frozen=True)
class PunchSegment:
    start_mm: float
    end_mm: float

    def __post_init__(self):
        if self.end_mm <= self.start_mm:
            raise ClampValidatorError(
                f"PunchSegment end_mm ({self.end_mm}) must be greater than start_mm ({self.start_mm})"
            )

    @property
    def length_mm(self) -> float:
        return self.end_mm - self.start_mm


@dataclass(frozen=True)
class ClampValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def validate_punch_chain(
    chain: list[PunchSegment],
    config: ClampConfig,
) -> ClampValidationResult:
    """
    Validate punch chain against clamp constraints.

    Rules applied in order (first failure category stops):
      1. Empty chain -> error.
      2. Chain order & gaps (end_i != start_{i+1} within joint_tolerance) -> error.
      3. Minimum clamps under the chain (blocks whose [center-W/2, center+W/2]
         intersects [chain[0].start, chain[-1].end]) -> error.
      4. All joints (chain[i].end for i in 0..n-2) inside some clamp zone -> error per joint.
      5. End overhangs:
         overhang_left = first_covering_block.start - chain[0].start (if chain[0].start < first_covering_block.start)
         overhang_right = chain[-1].end - last_covering_block.end (if chain[-1].end > last_covering_block.end)
         each overhang <= overhang_ratio * corresponding punch length -> error per end.
    """
    # Rule 1: Empty chain
    if not chain:
        return ClampValidationResult(valid=False, errors=("Punch chain cannot be empty",))

    chain_start = chain[0].start_mm
    chain_end = chain[-1].end_mm

    # Rule 2: Gaps or ordering (only if no_gaps_between_punches is enabled)
    if config.no_gaps_between_punches:
        for i in range(len(chain) - 1):
            curr_end = chain[i].end_mm
            next_start = chain[i + 1].start_mm
            gap = next_start - curr_end
            if abs(gap) > config.joint_tolerance_mm:
                return ClampValidationResult(
                    valid=False,
                    errors=(f"Gap or overlap between punch {i} (end={curr_end}) and {i+1} (start={next_start}): {gap:.3f}mm > tolerance {config.joint_tolerance_mm}mm",)
                )

    # Compute clamp zones: [center - W/2, center + W/2]
    half_w = config.width_mm / 2.0
    clamp_zones: List[Tuple[float, float, float]] = []  # (start, end, center)
    for c in config.centres_mm:
        clamp_zones.append((c - half_w, c + half_w, c))

    # Rule 3: Minimum clamps intersecting the punch chain
    # Intersection condition between [z_start, z_end] and [chain_start, chain_end]:
    # max(z_start, chain_start) < min(z_end, chain_end) + tolerance
    intersecting_blocks: List[Tuple[float, float, float]] = []
    for z_start, z_end, c in clamp_zones:
        if z_end >= chain_start - config.joint_tolerance_mm and z_start <= chain_end + config.joint_tolerance_mm:
            intersecting_blocks.append((z_start, z_end, c))

    if len(intersecting_blocks) < config.min_clamps_under_punch:
        return ClampValidationResult(
            valid=False,
            errors=(
                f"Insufficient clamp blocks under punch chain: required at least {config.min_clamps_under_punch}, "
                f"found {len(intersecting_blocks)} (chain=[{chain_start:.1f}, {chain_end:.1f}])",
            ),
        )

    # Rule 4: Joints must fall inside a clamp zone
    if config.joints_must_be_on_clamps and len(chain) > 1:
        joint_errors = []
        for i in range(len(chain) - 1):
            joint_x = chain[i].end_mm
            on_clamp = False
            for z_start, z_end, _ in clamp_zones:
                if (z_start - config.joint_tolerance_mm) <= joint_x <= (z_end + config.joint_tolerance_mm):
                    on_clamp = True
                    break
            if not on_clamp:
                joint_errors.append(
                    f"Joint {i} at x={joint_x:.2f}mm falls in gap between clamps (not on any clamp zone)"
                )
        if joint_errors:
            return ClampValidationResult(valid=False, errors=tuple(joint_errors))

    # Rule 5: Overhang check for end punches
    first_block = intersecting_blocks[0]
    last_block = intersecting_blocks[-1]

    overhang_errors = []

    # Left overhang
    first_block_start = first_block[0]
    if chain_start < first_block_start:
        overhang_left = first_block_start - chain_start
        max_allowed_left = config.overhang_ratio * chain[0].length_mm
        if overhang_left > max_allowed_left + config.joint_tolerance_mm:
            overhang_errors.append(
                f"Left punch overhang {overhang_left:.2f}mm exceeds {config.overhang_ratio*100:.0f}% "
                f"of punch length ({max_allowed_left:.2f}mm for length {chain[0].length_mm:.2f}mm)"
            )

    # Right overhang
    last_block_end = last_block[1]
    if chain_end > last_block_end:
        overhang_right = chain_end - last_block_end
        max_allowed_right = config.overhang_ratio * chain[-1].length_mm
        if overhang_right > max_allowed_right + config.joint_tolerance_mm:
            overhang_errors.append(
                f"Right punch overhang {overhang_right:.2f}mm exceeds {config.overhang_ratio*100:.0f}% "
                f"of punch length ({max_allowed_right:.2f}mm for length {chain[-1].length_mm:.2f}mm)"
            )

    if overhang_errors:
        return ClampValidationResult(valid=False, errors=tuple(overhang_errors))

    return ClampValidationResult(valid=True, errors=(), warnings=())


def validate_config_dict(data: dict) -> ClampConfig:
    """Validate the library's versioned clamp settings using the standard library."""
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ClampValidatorError("Unsupported clamp config schema version")
    return ClampConfig.from_dict(data)


def load_clamp_config(library_root: Path) -> ClampConfig:
    """
    Load <library_root>/config.json -> ClampConfig.
    If config.json missing -> DEFAULT config with empty positions.
    Raises ClampValidatorError on malformed JSON or invalid fields.
    """
    config_file = Path(library_root) / "config.json"
    if not config_file.exists():
        return ClampConfig.from_defaults(positions_mm=())

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ClampValidatorError(f"Failed to read JSON from {config_file}: {e}") from e

    return validate_config_dict(data)


def save_clamp_config(library_root: Path, config: ClampConfig) -> None:
    """
    Write <library_root>/config.json from ClampConfig.
    Creates parent directories if needed.
    Atomic write (tmp file + os.replace).
    """
    lib_path = Path(library_root)
    lib_path.mkdir(parents=True, exist_ok=True)
    config_file = lib_path / "config.json"
    data = config.to_dict()
    validate_config_dict(data)

    tmp_file = None
    try:
        with NamedTemporaryFile(mode="w", dir=lib_path, suffix=".tmp",
                                encoding="utf-8", delete=False) as f:
            tmp_file = Path(f.name)
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_file, config_file)
    except Exception as e:
        raise ClampValidatorError(f"Failed to save config to {config_file}: {e}") from e
    finally:
        if tmp_file is not None:
            tmp_file.unlink(missing_ok=True)
