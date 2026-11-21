"""Operator-recorded press-brake bending recipes for *matching* unfold geometry.

A recipe is an ordering and set of 0/180 loading preferences, NEVER a proof
of machine clearance. Exact shape matching is intentionally conservative:
- same unfolded outer contour and segmented rigid panels
- same real bend axes, final angles, thickness and bend radii
- same physical panel relationships and staged operation types/targets
- invariant under planar rigid translation and rotation, CAD ID renumbering
- NO mirror, scaling, 'similar enough' geometry, or manufacturing certification

No FreeCAD/Qt/OCC dependencies. JSON templates are stored in a user's home
folder and are never silently created from an unverified FAST candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from json import dump, dumps, load
from math import hypot, isfinite
from pathlib import Path
import os
import tempfile
from uuid import uuid4

from .fast_bfm_beam_search import FastCandidate

SCHEMA = "smbs.fast_bend_templates.v1"
QUANTUM_MM = 0.05
MAX_TEMPLATES = 200


class BendTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class GeometryIdentity:
    digest: str
    ordered_operation_ids: tuple[str, ...]
    bend_count: int


@dataclass(frozen=True)
class TemplateMatch:
    candidate: FastCandidate
    title: str
    template_id: str
    source_tooling: dict

    def to_dict(self):
        return {
            "title": self.title,
            "template_id": self.template_id,
            "source_tooling": dict(self.source_tooling),
            "candidate": self.candidate.to_dict(),
            "evidence": "OPERATOR_RECIPE_GEOMETRY_MATCH_UNVERIFIED_3D",
        }


def default_template_path() -> Path:
    """Persistent user-owned path independent of FreeCAD addon installation."""
    return Path.home() / ".smbs" / "bend_templates.v1.json"


def _point(point):
    try:
        x, y = float(point[0]), float(point[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise BendTemplateError("invalid_2d_point") from exc
    if not isfinite(x) or not isfinite(y):
        raise BendTemplateError("nonfinite_2d_point")
    return x, y


def _quantized(value):
    if not isfinite(float(value)):
        raise BendTemplateError("nonfinite_geometry")
    return int(round(float(value) / QUANTUM_MM))


def _sorted_points(points, frame):
    return sorted(set(frame(_point(p)) for p in points))


def geometry_identity(fold_state, operations, outline_xy,
                      panel_vertices_xy) -> GeometryIdentity:
    """Canonical rigid 2D identity with an ID-independent bend index map.

    Try every oriented bend axis as the frame X direction. The lexicographically
    smallest complete normalized geometry is canonical, including the map
    from canonical operation indices back to current SheetMetal operation IDs.
    Distinct coincident physical bends are rejected as ambiguous rather than
    assigned a fabricated source ID.
    """
    ops = tuple(operations)
    outline = tuple(_point(p) for p in outline_xy)
    if len(outline) > 3 and outline[-1] == outline[0]:
        outline = outline[:-1]
    if len(outline) < 3 or not ops:
        raise BendTemplateError("full_outer_outline_and_operations_required")
    panels = dict(panel_vertices_xy or {})
    if not panels or set(panels) != set(fold_state.graph.panels):
        raise BendTemplateError("all_rigid_panel_polygons_required")
    for pts in panels.values():
        if len(pts) < 3:
            raise BendTemplateError("incomplete_panel_polygon")
    axes = {}
    directions = []
    for op in ops:
        bend = fold_state.bends.get(op.bend_id)
        if bend is None or getattr(bend, "bend_zone", None) is None:
            raise BendTemplateError("authoritative_bend_zone_required")
        try:
            a, b = (_point(point) for point in bend.axis_xyz[:2])
            length = hypot(b[0] - a[0], b[1] - a[1])
        except (IndexError, TypeError) as exc:
            raise BendTemplateError("invalid_bend_axis") from exc
        if length < 1e-8:
            raise BendTemplateError("degenerate_bend_axis")
        if op.operation_id in axes:
            raise BendTemplateError("duplicate_operation_id")
        axes[op.operation_id] = (a, b, op, bend)
        direction = ((b[0]-a[0])/length, (b[1]-a[1])/length)
        directions.extend((direction, (-direction[0],-direction[1])))
    # A stable origin avoids subtracting large sheet coordinates only after
    # quantization. Translation is further removed via the contour min bounds.
    ox = sum(x for x, _ in outline) / len(outline)
    oy = sum(y for _, y in outline) / len(outline)
    best_serial = None
    best_order = ()
    for ux, uy in directions:
        def rotated(pt):
            px, py = pt[0]-ox, pt[1]-oy
            return ux*px+uy*py, -uy*px+ux*py
        local_outline = tuple(rotated(point) for point in outline)
        dx = min(p[0] for p in local_outline)
        dy = min(p[1] for p in local_outline)
        def frame(pt):
            x, y = rotated(pt)
            return (_quantized(x-dx), _quantized(y-dy))
        contour = _sorted_points(outline, frame)
        # Panel names are not source IDs; assign stable geometric numbers
        # by their full rigid 2D contours.
        panel_items = []
        for panel_id, vertices in panels.items():
            panel_items.append((tuple(_sorted_points(vertices,frame)),panel_id))
        panel_items.sort(key=lambda item:item[0])
        if len(set(item[0] for item in panel_items)) != len(panel_items):
            raise BendTemplateError("ambiguous_identical_rigid_panels")
        panel_index = {name:i for i,(_shape,name) in enumerate(panel_items)}
        per_operation = []
        for op_id, (a, b, op, bend) in axes.items():
            zone = bend.bend_zone
            step_geometry = (
                min(frame(a), frame(b)), max(frame(a), frame(b)),
                _quantized(float(op.target_angle_deg)),
                str(op.kind),
                _quantized(float(zone.thickness_mm)),
                _quantized(float(zone.inner_radius_mm)),
                _quantized(float(zone.outer_radius_mm)),
                panel_index[bend.fixed_panel],
                panel_index[bend.moving_panel],
            )
            per_operation.append((step_geometry,op_id))
        per_operation.sort(key=lambda item:item[0])
        if len(set(item[0] for item in per_operation)) != len(per_operation):
            raise BendTemplateError("ambiguous_duplicate_bend_features")
        profile = [
            SCHEMA, contour, [p[0] for p in panel_items],
            [item[0] for item in per_operation],
        ]
        serial = dumps(profile,sort_keys=True,separators=(",",":"))
        if best_serial is None or serial < best_serial:
            best_serial = serial
            best_order = tuple(item[1] for item in per_operation)
    if best_serial is None:
        raise BendTemplateError("no_canonical_geometry")
    return GeometryIdentity(
        sha256(best_serial.encode("utf-8")).hexdigest(),
        best_order, len(fold_state.bends),
    )


def _read(path):
    if not path.exists():
        return []
    try:
        with path.open("r",encoding="utf-8") as stream:
            data = load(stream)
    except (OSError, ValueError, TypeError) as exc:
        raise BendTemplateError("cannot_read_template_library") from exc
    if not isinstance(data,dict) or data.get("schema") != SCHEMA:
        raise BendTemplateError("unsupported_template_library_schema")
    records = data.get("templates")
    if not isinstance(records,list) or len(records) > MAX_TEMPLATES:
        raise BendTemplateError("invalid_template_library_records")
    return records


def store_operator_template(fold_state, operations, outline_xy,
                            panel_vertices_xy, operation_order,
                            reverse_by_operation, *, title,
                            tooling=None, path=None):
    """Store only on explicit operator request, never from FAST auto-results.

    Returns the saved public metadata. A recorded solution is not a safety
    certificate or automatically 3D-verified.
    """
    ident = geometry_identity(
        fold_state, operations, outline_xy, panel_vertices_xy)
    order = tuple(operation_order)
    ids = ident.ordered_operation_ids
    if len(order) != len(ids) or set(order) != set(ids):
        raise BendTemplateError("template_requires_complete_operation_order")
    if not isinstance(title,str) or not title.strip() or len(title)>100:
        raise BendTemplateError("template_title_required_max_100_chars")
    flags = dict(reverse_by_operation)
    if any(k not in ids or type(v) is not bool for k,v in flags.items()):
        raise BendTemplateError("invalid_template_orientation")
    ordinal = {op_id:i for i,op_id in enumerate(ids)}
    record = {
        "id":str(uuid4()),
        "title":title.strip(),
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "shape_sha256":ident.digest,
        "bend_count":ident.bend_count,
        "operation_indices":[ordinal[op_id] for op_id in order],
        "reverse_by_index":{str(i):flags.get(op_id,False)
                            for op_id,i in ordinal.items()},
        "source_tooling":{
            "punch":str((tooling or {}).get("punch","")),
            "die":str((tooling or {}).get("die","")),
        },
        "provenance":"OPERATOR_RECORDED_NOT_MACHINE_CERTIFIED",
    }
    dst = Path(path) if path is not None else default_template_path()
    records = _read(dst)
    if len(records)>=MAX_TEMPLATES:
        raise BendTemplateError("template_library_capacity_reached")
    # Replace only exact same named recipe for this shape, do not accumulate
    # repeated clicks; different named validated operations remain distinct.
    records = [item for item in records if not (
        isinstance(item,dict) and
        item.get("title")==record["title"] and
        item.get("shape_sha256")==record["shape_sha256"]
    )]
    records.append(record)
    dst.parent.mkdir(parents=True,exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",
                dir=dst.parent,delete=False,prefix=".bend_templates_",
                suffix=".tmp") as stream:
            temp_name = stream.name
            dump({"schema":SCHEMA,"templates":records},stream,
                 ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name,dst)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)
    return {
        "title":record["title"],"template_id":record["id"],
        "shape_sha256":ident.digest,
        "path":str(dst),
        "evidence":record["provenance"],
    }


def find_operator_templates(fold_state, operations, outline_xy,
                            panel_vertices_xy, *, path=None,
                            max_matches=5):
    """Load exact geometric matches only. Every result still needs Stage B/3D."""
    ident = geometry_identity(
        fold_state, operations, outline_xy,panel_vertices_xy)
    records = _read(Path(path) if path is not None
                    else default_template_path())
    matched = []
    seen = set()
    for record in reversed(records):
        if (not isinstance(record,dict)
                or record.get("shape_sha256")!=ident.digest
                or record.get("bend_count")!=ident.bend_count
                or record.get("provenance")!=
                    "OPERATOR_RECORDED_NOT_MACHINE_CERTIFIED"):
            continue
        indices = record.get("operation_indices")
        if (not isinstance(indices,list)
                or any(type(i) is not int for i in indices)
                or sorted(indices)!=list(range(len(ident.ordered_operation_ids)))):
            continue
        poses = record.get("reverse_by_index")
        if (not isinstance(poses,dict) or
                set(poses)!=set(str(i) for i in range(len(indices)))
                or any(type(v) is not bool for v in poses.values())):
            continue
        order = tuple(ident.ordered_operation_ids[i] for i in indices)
        reverse = tuple((ident.ordered_operation_ids[i],poses[str(i)])
                        for i in indices)
        if (order,reverse) in seen:
            continue
        seen.add((order,reverse))
        # A known operator recipe is a search priority, NOT proof of
        # absence of sheet/tool collision in the current machine setup.
        candidate = FastCandidate(
            order,reverse,0.0,0.0,
            evidence="OPERATOR_TEMPLATE_MATCH_UNVERIFIED_3D",
        )
        matched.append(TemplateMatch(
            candidate, str(record.get("title","Шаблон"))[:100],
            str(record.get("id","")),
            dict(record.get("source_tooling") or {}),
        ))
        if len(matched)>=max_matches:
            break
    return tuple(matched)
