"""Read-only physical-face inventory for the 2D SheetMetal workflow.

Reuses the physical-face pairing principle from
smbs_core.Geometry.feature_extractor.FeatureExtractor._group_panels,
using native FreeCAD Part geometry instead of the pythonOCC backend.

No automatic 2D-to-3D assignment: unfolded areas and folded surface areas
are not generally equivalent, and a face pair is not a bend correspondence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt, isfinite


@dataclass(frozen=True)
class PhysicalFace:
    index: int
    area_mm2: float
    centroid_xyz: tuple
    normal_xyz: tuple


@dataclass(frozen=True)
class ThicknessPair:
    face_a: int
    face_b: int
    separation_mm: float
    normal_dot: float
    transverse_mm: float


@dataclass(frozen=True)
class PhysicalInventory:
    thickness_mm: float
    planar_faces: tuple
    thickness_pairs: tuple
    cylindrical_face_indices: tuple
    unpaired_planar_indices: tuple

    def to_dict(self):
        return {
            "thickness_mm": self.thickness_mm,
            "planar_faces": [asdict(f) for f in self.planar_faces],
            "thickness_pairs": [asdict(p) for p in self.thickness_pairs],
            "cylindrical_face_indices": list(self.cylindrical_face_indices),
            "unpaired_planar_indices": list(self.unpaired_planar_indices),
        }


class PhysicalInventoryError(ValueError):
    pass


def _vec(v):
    return (float(v.x), float(v.y), float(v.z))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return sqrt(_dot(a, a))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _surface_kind(surface):
    return type(surface).__name__.lower()


def inspect_physical_faces(shape, thickness_mm, *,
                           thickness_tolerance_fraction=0.25,
                           normal_tolerance=0.01,
                           transverse_factor=2.0):
    """Inventory planar sheet faces and plausible opposite-side pairs.

    The geometric thresholds follow the earlier FeatureExtractor heuristic.
    Pairing is evidence only; it must not be treated as verified panel IDs.
    The caller must provide thickness from SheetMetal or a validated model.
    """
    t = float(thickness_mm)
    if not isfinite(t) or t <= 0:
        raise PhysicalInventoryError("Positive finite SheetMetal thickness required")
    if shape is None or shape.isNull():
        raise PhysicalInventoryError("Valid source 3D shape required")
    if not shape.isValid():
        raise PhysicalInventoryError("Source 3D shape is invalid")

    planes = []
    cylinders = []
    for index, face in enumerate(shape.Faces, start=1):
        kind = _surface_kind(face.Surface)
        if "cylinder" in kind:
            cylinders.append(index)
        elif "plane" in kind:
            c = _vec(face.CenterOfMass)
            try:
                n = _vec(face.normalAt(0, 0))
            except Exception as exc:
                raise PhysicalInventoryError(
                    "Cannot evaluate normal of Face{}".format(index)
                ) from exc
            magnitude = _norm(n)
            if magnitude < 1e-12:
                raise PhysicalInventoryError("Zero normal at Face{}".format(index))
            n = tuple(x / magnitude for x in n)
            planes.append(PhysicalFace(index, float(face.Area), c, n))

    candidates = []
    for i, a in enumerate(planes):
        for b in planes[i + 1:]:
            dot = _dot(a.normal_xyz, b.normal_xyz)
            if abs(dot + 1.0) > normal_tolerance:
                continue
            displacement = _sub(b.centroid_xyz, a.centroid_xyz)
            along = abs(_dot(displacement, a.normal_xyz))
            transverse = _norm(tuple(
                displacement[k] - _dot(displacement, a.normal_xyz) * a.normal_xyz[k]
                for k in range(3)
            ))
            if (abs(along - t) <= thickness_tolerance_fraction * t
                    and transverse < transverse_factor * t):
                candidates.append(ThicknessPair(
                    a.index, b.index, along, dot, transverse
                ))
    # Do not force one-to-one pairing: several plausible partners are an
    # ambiguity the diagnostic should expose rather than silently resolve.
    paired = {index for pair in candidates for index in (pair.face_a, pair.face_b)}
    return PhysicalInventory(
        thickness_mm=t,
        planar_faces=tuple(planes),
        thickness_pairs=tuple(candidates),
        cylindrical_face_indices=tuple(cylinders),
        unpaired_planar_indices=tuple(
            face.index for face in planes if face.index not in paired
        ),
    )


def inspect_document(source_object_name, thickness_mm, document=None):
    """Convenience entry point for the FreeCAD Python console."""
    import FreeCAD as App
    doc = document if document is not None else App.ActiveDocument
    if doc is None:
        raise PhysicalInventoryError("No active FreeCAD document")
    obj = doc.getObject(source_object_name)
    if obj is None or not hasattr(obj, "Shape"):
        raise PhysicalInventoryError(
            "Source object {!r} has no Shape".format(source_object_name)
        )
    return inspect_physical_faces(obj.Shape, thickness_mm)
