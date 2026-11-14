"""Conservative FreeCAD PanelBuilder for planar SheetMetal unfolded sketches.

Builds material islands separated by finite bend-axis strips. Never guesses
topology: a bend must separate exactly two distinct panels. The strip is a
computational separator, NOT a physical bend allowance or a final panel shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from smbs_core.kinematics2d.axis_matching import match_bend_axes
from smbs_core.kinematics2d.bend_zone import (
    BendZone,
    BendZoneError,
    bend_parameters,
    make_bend_zone,
)


class PanelBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Panel:
    panel_id: str
    area_mm2: float
    centroid_xy: tuple


@dataclass(frozen=True)
class PanelConnection:
    bend_id: str
    panel_a: str
    panel_b: str
    angle_deg: float
    radius_mm: float
    axis_xy: tuple
    bend_zone: BendZone | None = None


@dataclass(frozen=True)
class PanelBuildResult:
    panels: tuple
    connections: tuple
    axis_matches: tuple
    # Geometry retained only for FreeCAD visualization; not serializable.
    panel_faces: tuple
    bend_zones: tuple = ()
    bend_zone_faces: tuple = ()
    generation_token: int = 0
    # Full unfolded outer boundary; retained independently of partition panels.
    outline_vertices_xy: tuple = ()


def _xy(p):
    return (float(p.x), float(p.y))


def _segment(edge):
    vertices = edge.Vertexes
    if len(vertices) != 2:
        raise PanelBuildError("Bend sketch must contain straight edges")
    return _xy(vertices[0].Point), _xy(vertices[1].Point)


def _strip(Part, App, a, b, width, extension):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    if length <= 0:
        raise PanelBuildError("Zero-length bend axis")
    ux, uy = dx / length, dy / length
    nx, ny = -uy * width / 2, ux * width / 2
    x0, y0 = a[0] - ux * extension, a[1] - uy * extension
    x1, y1 = b[0] + ux * extension, b[1] + uy * extension
    corners = [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
               (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]
    points = [App.Vector(x, y, 0) for x, y in corners]
    return Part.Face(Part.makePolygon(points + [points[0]]))


def _contains(face, point, tol):
    import FreeCAD as App
    return bool(face.isInside(App.Vector(point[0], point[1], 0), tol, True))


def _side_panel(faces, a, b, side, sample_mm, tol):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    probe = (mx - side * dy / length * sample_mm,
             my + side * dx / length * sample_mm)
    hits = [i for i, face in enumerate(faces) if _contains(face, probe, tol)]
    if len(hits) != 1:
        raise PanelBuildError("Probe at {} has {} panels; expected 1".format(probe, len(hits)))
    return hits[0]


def build_panels(unfold_name="Body_Unfold", outline_name=None, bends_name=None,
                 document=None, strip_width_mm=0.02, extension_mm=0.2):
    """Split a planar closed outline using canonical matched bend sketch lines.

    Fails closed for ambiguous/non-partitioning bends, curved or multiple
    outlines, or coordinate-frame mismatch. Does not determine moving side,
    collision clearance, or 3D press feasibility.
    """
    import FreeCAD as App
    import Part
    from freecad_smbs.integration.sheetmetal_unfold_reader import read_unfold

    doc = document if document is not None else App.ActiveDocument
    if doc is None:
        raise PanelBuildError("No active document")
    if not all(isfinite(v) and v > 0 for v in (strip_width_mm, extension_mm)):
        raise PanelBuildError("Positive finite strip width and extension required")
    data = read_unfold(unfold_name, doc)
    unfold = doc.getObject(unfold_name)
    # First use the sketches owned by THIS unfold. FreeCAD may assign numeric
    # internal-name suffixes after a remodel (e.g. ..._Sketch001) while the
    # visible Label and UnfoldSketches links continue to identify their roles.
    # Never use sketches from another unfold or pick an arbitrary first match.
    if outline_name is None or bends_name is None:
        import re

        def _as_object(entry):
            if isinstance(entry, str):
                return doc.getObject(entry)
            if isinstance(entry, (tuple, list)) and entry:
                return _as_object(entry[0])
            return entry if hasattr(entry, "Name") else None

        def _unique_objects(entries):
            seen = set()
            result = []
            for entry in entries:
                obj = _as_object(entry)
                if obj is None or not hasattr(obj, "Shape"):
                    continue
                name = str(obj.Name)
                if name not in seen:
                    result.append(obj)
                    seen.add(name)
            return result

        # The property is populated by SheetMetal when separate sketches
        # are generated. An empty property is not considered a valid link set.
        linked = _unique_objects(getattr(unfold, "UnfoldSketches", ()) or ())
        if linked:
            candidates = linked
            source_kind = "UnfoldSketches links"
        else:
            # Older/partially recomputed SheetMetal objects may not expose
            # UnfoldSketches. Accept numeric FreeCAD internal-name suffixes.
            pattern = re.compile(
                r"^" + re.escape(unfold.Name) +
                r"_Sketch(?:_Bends|_Labels)?\d*$"
            )
            candidates = _unique_objects(
                o for o in doc.Objects if pattern.fullmatch(str(o.Name))
            )
            source_kind = "generated sketch names"

        def _role(obj):
            # Labels can retain the original name when FreeCAD auto-renames
            # the internal Name to avoid duplicate names after remodeling.
            names = (str(getattr(obj, "Name", "")),
                     str(getattr(obj, "Label", "")))
            for value in names:
                if re.search(r"_Sketch_Bends\d*$", value):
                    return "bends"
                if re.search(r"_Sketch_Labels\d*$", value):
                    return "labels"
            for value in names:
                if re.search(r"_Sketch\d*$", value):
                    return "outline"
            return None

        outline_candidates = [o for o in candidates if _role(o) == "outline"]
        bends_candidates = [o for o in candidates if _role(o) == "bends"]
        if outline_name is None:
            if len(outline_candidates) != 1:
                raise PanelBuildError(
                    "Cannot identify unique outline sketch for {} via {}: "
                    "found {} candidate(s), names={}. Recompute SheetMetal "
                    "Unfold with GenerateSketch and SeparateSketchLayers "
                    "enabled, or supply outline_name explicitly.".format(
                        unfold.Name, source_kind, len(outline_candidates),
                        [o.Name for o in candidates],
                    )
                )
            outline_name = outline_candidates[0].Name
        if bends_name is None:
            if len(bends_candidates) != 1:
                raise PanelBuildError(
                    "Cannot identify unique bend-lines sketch for {} via {}: "
                    "found {} candidate(s), names={}. Recompute SheetMetal "
                    "Unfold with GenerateSketch and SeparateSketchLayers "
                    "enabled, or supply bends_name explicitly.".format(
                        unfold.Name, source_kind, len(bends_candidates),
                        [o.Name for o in candidates],
                    )
                )
            bends_name = bends_candidates[0].Name
    outline = doc.getObject(outline_name)
    bend_sketch = doc.getObject(bends_name)
    if outline is None or bend_sketch is None:
        raise PanelBuildError("Unfold sketch object missing")
    outline_shape = outline.Shape
    lines = tuple(_segment(edge) for edge in bend_sketch.Shape.Edges)
    canonical = tuple((b.start_xyz[:2], b.end_xyz[:2]) for b in data.bends)
    if len(lines) != len(canonical) and data.sketch_outline is not None:
        outline_shape = data.sketch_outline
        lines = canonical
    if len(outline_shape.Wires) != 1 or not outline_shape.Wires[0].isClosed():
        raise PanelBuildError("Expected exactly one closed outline wire")
    if len(outline_shape.Edges) < 3:
        raise PanelBuildError("Invalid outline")
    # Sample the entire ordered wire (including arcs), rather than concatenating
    # edge vertices whose individual orientations are not guaranteed.
    try:
        outline_points = outline_shape.Wires[0].discretize(Deflection=0.05)
    except Exception as exc:
        raise PanelBuildError("Cannot discretize unfolded outer outline") from exc
    outline_vertices_xy = tuple((float(p.x), float(p.y)) for p in outline_points)
    if len(outline_vertices_xy) < 3:
        raise PanelBuildError("Unfolded outer outline has insufficient points")
    try:
        sheet = Part.Face(outline_shape.Wires[0])
    except Exception as exc:
        raise PanelBuildError("Cannot construct planar face from outline") from exc
    if not sheet.isValid() or sheet.Area <= 0:
        raise PanelBuildError("Invalid planar outline face")
    if abs(sheet.BoundBox.ZLength) > 1e-5:
        raise PanelBuildError("Outline is not in a single XY plane")
    # Geometry is in world coordinates via Shape; align to z=0 for planar OCC.
    z = sheet.CenterOfMass.z
    if abs(z) > 1e-5:
        raise PanelBuildError("Outline plane must be z=0")
    axis_matches = match_bend_axes(canonical, lines)
    # Reject source/sketch frame mismatch; never silently shift canonical data.
    cutters = []
    for m in axis_matches:
        a, b = lines[m.sketch_index]
        cutters.append(_strip(Part, App, a, b, strip_width_mm, extension_mm))
    material = sheet
    for cutter in cutters:
        material = material.cut(cutter)
    faces = tuple(f for f in material.Faces if f.Area > 1e-5)
    if len(faces) < 2 or not all(f.isValid() for f in faces):
        raise PanelBuildError("Cutting bend strips did not produce valid panels")
    # Sample beyond the separator; enough to clear its half-width.
    sample = max(strip_width_mm * 2, 0.05)
    graph = []
    zones = []
    zone_cutters = []
    zone_faces = []
    for bend, match in zip(data.bends, axis_matches):
        a, b = lines[match.sketch_index]
        left = _side_panel(faces, a, b, +1, sample, 1e-6)
        right = _side_panel(faces, a, b, -1, sample, 1e-6)
        if left == right:
            raise PanelBuildError("{} does not separate two panels".format(bend.bend_id))
        try:
            params = bend_parameters(
                bend.source_angle_deg,
                bend.radius_mm,
                bend.thickness_mm,
                bend.k_factor_ansi,
            )
            zone = make_bend_zone(bend.bend_id, (a, b), params)
        except BendZoneError as exc:
            raise PanelBuildError("Invalid bend zone for {}: {}".format(bend.bend_id, exc)) from exc
        if zone.bend_allowance_mm <= strip_width_mm:
            raise PanelBuildError(
                "Bend allowance ({:.5f} mm) for {} does not exceed topology strip width ({:.5f} mm)".format(
                    zone.bend_allowance_mm, bend.bend_id, strip_width_mm
                )
            )
        zcutter = _strip(Part, App, a, b, zone.bend_allowance_mm, extension_mm)
        zface = sheet.common(zcutter)
        if zface.Area <= 1e-5:
            raise PanelBuildError("Empty bend zone face for {}".format(bend.bend_id))
        for prev_zone, prev_face in zip(zones, zone_faces):
            if zface.common(prev_face).Area > 1e-5:
                raise PanelBuildError(
                    "Overlapping bend zones: {} and {}".format(prev_zone.bend_id, zone.bend_id)
                )
        zones.append(zone)
        zone_cutters.append((left, right, zcutter))
        zone_faces.append(zface)
        graph.append(PanelConnection(bend.bend_id, "P{:02d}".format(left + 1),
                                     "P{:02d}".format(right + 1),
                                     bend.fold_angle_deg, bend.radius_mm, (a, b), zone))
    rigid_faces = []
    for i, island in enumerate(faces):
        rigid_shape = island
        for left_idx, right_idx, zcutter in zone_cutters:
            if i in (left_idx, right_idx):
                rigid_shape = rigid_shape.cut(zcutter)
        subfaces = [f for f in rigid_shape.Faces if f.Area > 1e-5]
        if len(subfaces) != 1 or not subfaces[0].isValid():
            raise PanelBuildError(
                "Bend zones consume or split rigid panel P{:02d} (got {} subfaces)".format(
                    i + 1, len(subfaces)
                )
            )
        rigid_faces.append(subfaces[0])
    rigid_faces_t = tuple(rigid_faces)
    panels = tuple(Panel("P{:02d}".format(i + 1), float(f.Area),
                         _xy(f.CenterOfMass)) for i, f in enumerate(rigid_faces_t))
    return PanelBuildResult(
        panels,
        tuple(graph),
        axis_matches,
        rigid_faces_t,
        tuple(zones),
        tuple(zone_faces),
        outline_vertices_xy=outline_vertices_xy,
    )
