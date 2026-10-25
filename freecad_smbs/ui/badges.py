"""
badges.py - Status badges and honesty color mappings for SMBS FreeCAD Addon.

Headless-safe, pure Python constants and mappings without Qt/GUI dependencies.
"""
from __future__ import annotations

from typing import Dict, Tuple

# Badge text constants
BADGE_VERIFIED: str = "VERIFIED"
BADGE_UNVERIFIED: str = "UNVERIFIED"
BADGE_VERIFICATION_FAILED: str = "VERIFICATION_FAILED"
BADGE_PREVIEW_ONLY: str = "PREVIEW_ONLY (KL-12)"
BADGE_MACHINE_SIMPLIFIED: str = "MACHINE: SIMPLIFIED (KL-16 pending)"
BADGE_TOOLING_MISSING: str = "TOOLING: MISSING"

# Color mappings in RGBA (0.0 - 1.0)
BADGE_COLORS_RGBA: Dict[str, Tuple[float, float, float, float]] = {
    BADGE_VERIFIED: (0.0, 0.6, 0.0, 1.0),
    BADGE_UNVERIFIED: (0.5, 0.5, 0.5, 1.0),
    BADGE_VERIFICATION_FAILED: (1.0, 0.5, 0.0, 1.0),
    BADGE_PREVIEW_ONLY: (0.9, 0.7, 0.0, 1.0),
    BADGE_MACHINE_SIMPLIFIED: (0.7, 0.7, 0.7, 1.0),
    BADGE_TOOLING_MISSING: (0.8, 0.0, 0.0, 1.0),
}

# Hex color mappings for web / Qt stylesheets
BADGE_COLORS_HEX: Dict[str, str] = {
    BADGE_VERIFIED: "#2E7D32",          # Green
    BADGE_UNVERIFIED: "#757575",        # Gray
    BADGE_VERIFICATION_FAILED: "#EF6C00",# Orange
    BADGE_PREVIEW_ONLY: "#F57F17",       # Amber / Yellow
    BADGE_MACHINE_SIMPLIFIED: "#616161",# Neutral Gray
    BADGE_TOOLING_MISSING: "#C62828",   # Red
}


def get_badge_rgba(badge: str) -> Tuple[float, float, float, float]:
    """Return RGBA tuple for the given badge name (defaults to gray)."""
    return BADGE_COLORS_RGBA.get(badge, (0.5, 0.5, 0.5, 1.0))


def get_badge_hex(badge: str) -> str:
    """Return Hex color code for the given badge name (defaults to gray)."""
    return BADGE_COLORS_HEX.get(badge, "#757575")
