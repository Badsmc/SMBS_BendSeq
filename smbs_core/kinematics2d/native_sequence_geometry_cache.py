"""Explicit per-Manual-run cache for the *geometry* shared by native bends.

Owned by a single synchronous selected-order check, not global to FreeCAD.
Never shares mutable FoldState, collision verdicts, or tool-specific results.
Full native sheet OCC solids are bounded (memory), and source identity
changes invalidate every cached BRep before new geometry is evaluated.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NativeSequenceGeometryCache:
    """Independent exact-BRep geometry cache across sequential D6 validators.

    Full-sheets are keyed by exact angles + source geometry; panel prisms
    and bend-zone shapes are never mutated in place by native validators.
    Never persist this object across independent documents or work sessions.
    """
    max_full_sheets: int = 16
    full_sheets: OrderedDict = field(default_factory=OrderedDict)
    panel_prisms: dict = field(default_factory=dict)
    bend_zones: dict = field(default_factory=dict)
    source_identity: Any = None
    source_resets: int = 0

    def __post_init__(self) -> None:
        if (isinstance(self.max_full_sheets, bool)
                or not isinstance(self.max_full_sheets, int)
                or self.max_full_sheets < 1
                or self.max_full_sheets > 128):
            raise ValueError("max_full_sheets must be an integer from 1 to 128")

    def clear(self) -> None:
        """Drop retained shapes; a new FoldState signature must be acquired."""
        self.full_sheets.clear()
        self.panel_prisms.clear()
        self.bend_zones.clear()
        self.source_identity = None

    def bind_source(self, identity: Any) -> bool:
        """Clear all reused solids if the source geometry identity changes."""
        if identity is None:
            raise ValueError("shared native cache requires source identity")
        if self.source_identity == identity:
            return False
        self.clear()
        self.source_identity = identity
        self.source_resets += 1
        return True

    def statistics(self) -> dict:
        """Non-sensitive lifetime counters for the existing Desktop JSON log."""
        return {
            "scope": "ONE_MANUAL_NATIVE_CHECK_ONLY",
            "source_resets": self.source_resets,
            "full_sheet_entries": len(self.full_sheets),
            "panel_prism_entries": len(self.panel_prisms),
            "bend_zone_entries": len(self.bend_zones),
            "full_sheet_limit": self.max_full_sheets,
        }
