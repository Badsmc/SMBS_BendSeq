"""Read-only preflight for the explicitly selected backgauge contact face.

The user's `Body/Pad.Face6` is resolved on the Pad feature and then
checked against the *final* Body Shape, which has independent face indices.
This check validates CAD geometry only, not collision-free manufacturing.
"""
from __future__ import annotations

from pathlib import Path
import json


def inspect_backgauge_cad(library_dir=None):
    import FreeCAD as App
    import Part
    from smbs_core.kinematics2d.backgauge_cad_datum import (
        resolve_stop_face, z_rotation_to_operator_deg,
    )

    source_dir = (
        Path(library_dir).expanduser().resolve() if library_dir is not None
        else Path(__file__).resolve().parents[2] / "toollibrary" / "Backgauge"
    )
    path = source_dir / "Backgauge.FCStd"
    config_path = source_dir / "Backgauge.tooling.json"
    if not path.is_file() or not config_path.is_file():
        raise ValueError("Backgauge.FCStd / Backgauge.tooling.json missing")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("kind") != "backgauge" or data.get("source") != path.name:
        raise ValueError("Backgauge kind/source mismatch")
    datum = data.get("datums", {}).get("stop_face", {})

    active_name = getattr(App.ActiveDocument, "Name", None)
    doc = None
    try:
        doc = App.openDocument(str(path), True)
        bodies = [
            obj for obj in doc.Objects
            if obj.TypeId == "PartDesign::Body"
            and hasattr(obj, "Shape") and not obj.Shape.isNull()
            and obj.Shape.isValid() and len(obj.Shape.Solids) == 1
        ]
        if len(bodies) != 1:
            raise ValueError(
                "expected one valid final PartDesign::Body; found {}"
                .format(len(bodies))
            )
        body = bodies[0]
        index, point, normal = resolve_stop_face(
            doc, body, datum, App, Part
        )
        angle_deg = z_rotation_to_operator_deg(normal)
        rotation = App.Rotation(App.Vector(0, 0, 1), angle_deg)
        transformed = rotation.multVec(normal)
        if transformed.dot(App.Vector(0, 1, 0)) < .999:
            raise ValueError(
                "selected stop normal cannot be oriented toward operator"
            )
        shape = body.Shape
        bounds = shape.BoundBox
        return {
            "source": str(path),
            "object": body.Name,
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "source_feature": str(datum["source_object"]),
            "source_subelement": str(datum["subelement"]),
            "body_stop_face_index": index,
            "stop_face_index": index,
            "stop_face_origin_mm": [
                float(point.x), float(point.y), float(point.z)
            ],
            "stop_face_normal": [
                float(normal.x), float(normal.y), float(normal.z)
            ],
            "rotation_to_operator_z_deg": float(angle_deg),
            "mapped_normal_machine_xyz": [
                float(transformed.x),
                float(transformed.y),
                float(transformed.z),
            ],
            "bbox_mm": {
                "x": float(bounds.XLength),
                "y": float(bounds.YLength),
                "z": float(bounds.ZLength),
            },
            "status": "CAD_SOLID_AND_SELECTED_PAD_FACE_VALIDATED_ONLY",
            "collision_model_active": False,
        }
    finally:
        if doc is not None:
            App.closeDocument(doc.Name)
        if active_name and active_name in App.listDocuments():
            App.setActiveDocument(active_name)
