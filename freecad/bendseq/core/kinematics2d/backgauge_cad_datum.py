"""Resolve a user-selected PartDesign subelement to a real final Body face.

The FreeCAD selection subname `Pad.Face6` belongs to the Pad feature,
NOT necessarily Face6 of the final Body. An explicit source object,
subelement and picked point are checked against source and finished solids.
No guessed normals, implicit Face3 fallback or synthetic tool geometry.
"""
from __future__ import annotations

from math import atan2, degrees, isfinite
import re


class StopDatumError(ValueError):
    pass


def _coords(vector):
    return (float(vector.x), float(vector.y), float(vector.z))


def _normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def resolve_stop_face(doc, body, datum, app, part):
    """Return (final Body face index, picked point, outward source normal).

    A selected source feature may have its own face numbering. Verify that
    the chosen finite planar surface survives in the final Body; a source
    feature which was cut away is not a valid physical backgauge surface.
    """
    if not isinstance(datum, dict) or datum.get("kind") != "plane":
        raise StopDatumError("stop_face.kind=plane required")
    obj_name = datum.get("source_object")
    subname = datum.get("subelement")
    if not isinstance(obj_name, str) or not obj_name:
        raise StopDatumError(
            "stop_face.source_object required (selected: Pad)"
        )
    match = re.fullmatch(r"Face([1-9][0-9]*)", str(subname))
    if match is None:
        raise StopDatumError(
            "stop_face.subelement must be a feature face such as Face6"
        )
    origin = datum.get("origin")
    if (not isinstance(origin, list) or len(origin) != 3
            or any(isinstance(x, bool) or
                   not isinstance(x, (int, float)) or
                   not isfinite(float(x)) for x in origin)):
        raise StopDatumError("selected stop_face point must be 3 finite values")
    tol = float(datum.get("tolerance_mm", 0.05))
    if not isfinite(tol) or not 0 < tol <= 0.5:
        raise StopDatumError("invalid stop_face.tolerance_mm")
    feature = doc.getObject(obj_name)
    if feature is None or not hasattr(feature, "Shape"):
        raise StopDatumError(
            "selected stop feature not found: " + obj_name
        )
    if feature.Shape.isNull():
        raise StopDatumError("selected stop feature Shape is null")
    idx = int(match.group(1))
    if idx > len(feature.Shape.Faces):
        raise StopDatumError(
            "selected {}.{} missing (only {} feature faces)"
            .format(obj_name, subname, len(feature.Shape.Faces))
        )
    selected = feature.Shape.Faces[idx - 1]
    if not isinstance(selected.Surface, part.Plane):
        raise StopDatumError(
            "selected {}.{} is not planar".format(obj_name, subname)
        )
    point = app.Vector(*[float(x) for x in origin])
    source_dist = float(
        selected.distToShape(part.Vertex(point))[0]
    )
    if source_dist > tol:
        raise StopDatumError(
            "selected {}.{} point misses finite face by {:.4f} mm"
            .format(obj_name, subname, source_dist)
        )
    outward = _normal(selected)
    if (not isfinite(outward.Length) or outward.Length < .999
            or outward.Length > 1.001):
        raise StopDatumError("selected face has invalid outward normal")
    # A vertical end-stop can be mapped around Z without changing gravity.
    # A tilted face would require explicit R/Z machine mounting metadata;
    # do not silently rotate the whole 3D gauge out of the bed plane.
    if abs(float(outward.z)) > 0.01:
        raise StopDatumError(
            "selected stop is not vertical (normal_z={:.6f}); "
            "machine tilt and Z mounting are not configured"
            .format(float(outward.z))
        )
    # A caller must *not* trust an old sidecar normal from another face.
    # For this explicitly selected subelement, OCC is the source of truth.
    body_shape = body.Shape
    matching = []
    for body_index, face in enumerate(body_shape.Faces, 1):
        if not isinstance(face.Surface, part.Plane):
            continue
        if float(face.distToShape(part.Vertex(point))[0]) > tol:
            continue
        n = _normal(face)
        if float(n.dot(outward)) < .999:
            continue
        # Face6 may be split into multiple final Body surfaces; the
        # picked interior point must match exactly one survivor.
        matching.append(body_index)
    if len(matching) != 1:
        raise StopDatumError(
            "selected {}.{} at {} does not uniquely survive final Body "
            "(matching Body faces={}); CAD collision stays disabled"
            .format(obj_name, subname, origin, matching)
        )
    return matching[0], point, outward


def z_rotation_to_operator_deg(source_normal):
    """Z rotation sends source outward horizontal normal to machine +Y."""
    nx, ny, nz = _coords(source_normal)
    if abs(nz) > .01:
        raise StopDatumError("tilted source face requires explicit mounting")
    return degrees(atan2(nx, ny))
