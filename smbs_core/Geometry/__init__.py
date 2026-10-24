"""
BendSeq Geometry Package
Provides FreeCAD SheetMetal unfolding bridge and 3D CAD feature extraction.
"""

from .feature_extractor import FeatureExtractor
from .face_utils import find_largest_planar_face


__all__ = ["FeatureExtractor", "find_largest_planar_face"]
