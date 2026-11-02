"""Deterministic 2D cross-section kinematics for bend-sequence exploration.

This module does NOT certify 3D collisions or production eligibility.
"""
from .engine import Point2D, Segment2D, Bend2D, SectionState, fold_section, intersect_segments, self_intersections

__all__ = ["Point2D", "Segment2D", "Bend2D", "SectionState", "fold_section", "intersect_segments", "self_intersections"]
