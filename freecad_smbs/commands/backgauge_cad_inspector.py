"""Inspect the real Backgauge.FCStd CAD and verify its stop-face datum.

This is a read-only FreeCAD preflight, NOT a collision oracle. No BBox or
approximate rectangle may be reported as a verified finger.
"""
from __future__ import annotations

from pathlib import Path
import json
import math


def inspect_backgauge_cad(library_dir=None):
    """Validate source CAD solid and sidecar stop face, without touching user doc.

    Returns diagnostics; raises descriptive ValueError when the required
    physical contact surface is absent or cannot be validated.
    """
    import FreeCAD as App
    import Part

    source_dir = (
        Path(library_dir).resolve() if library_dir is not None
        else Path(__file__).resolve().parents[2] / "toollibrary" / "Backgauge"
    )
    cad_path = source_dir / "Backgauge.FCStd"
    json_path = source_dir / "Backgauge.tooling.json"
    if not cad_path.is_file() or not json_path.is_file():
        raise ValueError("Backgauge.FCStd / Backgauge.tooling.json missing")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if data.get("kind") != "backgauge" or data.get("source") != cad_path.name:
        raise ValueError("Backgauge source/kind mismatch")
    datum = data.get("datums", {}).get("stop_face", {})
    if datum.get("kind") != "plane":
        raise ValueError("Backgauge stop_face plane datum is missing")
    origin = datum.get("origin", ())
    direction = datum.get("normal", ())
    if (len(origin) != 3 or len(direction) != 3
            or any(not isinstance(n, (float, int)) or
                   isinstance(n, bool) or not math.isfinite(n)
                   for n in (*origin, *direction))):
        raise ValueError("Invalid stop_face origin or normal")
    norm = math.sqrt(sum(float(n)**2 for n in direction))
    if not 0.999 <= norm <= 1.001:
        raise ValueError("stop_face normal must have unit length")
    tol = float(datum.get("tolerance_mm", 0.05))
    if not math.isfinite(tol) or tol <= 0:
        raise ValueError("Invalid stop_face tolerance")
    point = App.Vector(*map(float, origin))
    normal = App.Vector(*map(float, direction))

    active = getattr(App, "ActiveDocument", None)
    active_name = getattr(active, "Name", None)
    doc = None
    try:
        doc = App.openDocument(str(cad_path), True)
        candidates = [
            obj for obj in doc.Objects
            if hasattr(obj, "Shape") and not obj.Shape.isNull()
            and getattr(obj.Shape, "Solids", ())
            and obj.Shape.isValid()
        ]
        # Prefer one assembled body over duplicated tree stages (Pad/Sketch).
        bodies = [o for o in candidates if o.TypeId == "PartDesign::Body"]
        obj = bodies[0] if len(bodies) == 1 else (
            candidates[-1] if len(candidates) == 1 else None
        )
        if obj is None:
            raise ValueError(
                "Cannot uniquely identify valid Backgauge solid/Body; "
                "candidates: " + ",".join(o.Name for o in candidates)
            )
        shape = obj.Shape.copy()
        faces = []
        for index, face in enumerate(shape.Faces, 1):
            if not isinstance(face.Surface, Part.Plane):
                continue
            # Surface.Axis may point opposite to the actual oriented face
            # normal. Verify the outward face normal, not just its plane.
            u_min, u_max, v_min, v_max = face.ParameterRange
            axis = face.normalAt(
                (u_min + u_max) / 2, (v_min + v_max) / 2
            )
            if axis.dot(normal) < 0.999:
                continue
            # Datum origin must actually lie on this contact face, not
            # just its infinite support plane.
            distance, _pairs, _info = face.distToShape(Part.Vertex(point))
            if distance <= tol:
                faces.append((index, face, axis))
        if len(faces) != 1:
            raise ValueError(
                "Expected exactly one planar CAD face at stop_face datum; "
                "found {} (not using a synthetic rectangle)".format(len(faces))
            )
        index, face, axis = faces[0]
        if axis.dot(normal) < 0.999:
            raise ValueError("CAD stop_face normal opposes metadata")
        bb = shape.BoundBox
        return {
            "source": str(cad_path),
            "object": obj.Name,
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "stop_face_index": index,
            "stop_face_origin_mm": list(map(float, origin)),
            "stop_face_normal": list(map(float, direction)),
            "bbox_mm": {
                "x": float(bb.XLength),
                "y": float(bb.YLength),
                "z": float(bb.ZLength),
            },
            "status": "CAD_SOLID_AND_STOP_FACE_VALIDATED_ONLY",
            "collision_model_active": False,
        }
    finally:
        if doc is not None:
            App.closeDocument(doc.Name)
        if active_name and active_name in App.listDocuments():
            App.setActiveDocument(active_name)
