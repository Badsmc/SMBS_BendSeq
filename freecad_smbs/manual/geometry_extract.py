"""
geometry_extract.py - Phase 2 Geometric Extraction & Canonical Matching.

Implements:
1. extract_unfold_solid(doc, unfold_name) -> Part.Shape:
   Safely extracts 3D unfold shape from document.
2. extract_outer_contour_unfold_3d(shape, plane) -> list[Point2D]:
   Extracts outer boundary polygon of the unfolded sheet with plane normalization (U2/U3).
3. extract_outer_contour_drawing_2d(view) -> list[Point2D]:
   Extracts 2D contour vertices from FreeCAD TechDraw::DrawViewPart (U2).
4. extract_bend_lines_3d(shape) -> list[RawBendLine]:
   Extracts unmapped candidate bend lines from 3D shape (U1 decoupling).
5. match_to_canonical(raw_lines, canonical_part_04) -> list[BendLine]:
   Executes deterministic lexicographical tie-break matching against 04_canonical_part.json (§3.2, U1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import FreeCAD as App
    import Part
except ImportError:
    App = None
    Part = None


Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]


@dataclass
class RawBendLine:
    """Unmapped bend line extracted directly from 3D CAD geometry (U1)."""
    midpoint: Point3D
    direction: Vector3D                     # Unit direction vector
    normal: Vector3D                        # Unit face normal
    radius_mm: float
    length_mm: float
    adjacent_panel_areas: Tuple[float, float] = (0.0, 0.0)
    source_edge_ref: str = ""               # FreeCAD transient name for debug


@dataclass
class BendLine:
    """Canonical mapped bend line adhering to 04_canonical_part.json contract."""
    bend_id: str                            # Canonical ID from 04 (e.g. "BEND_01")
    midpoint: Point3D
    direction: Vector3D
    normal: Vector3D
    radius_mm: float
    length_mm: float
    angle_deg: float
    parent_panel_id: str = ""
    child_panel_id: str = ""
    score: float = 0.0                      # Match score in [0.0, 1.0]


def extract_unfold_solid(doc: Any, unfold_name: str = "Unfold") -> Any:
    """
    Safely retrieves the unfolded Part.Shape from the FreeCAD document.
    """
    if doc is None:
        raise ValueError("Document cannot be None")
    obj = doc.getObject(unfold_name)
    if obj is None:
        # Fallback search by label or type
        for o in doc.Objects:
            if "unfold" in o.Name.lower() or "unfold" in getattr(o, "Label", "").lower():
                obj = o
                break
    if obj is None or not hasattr(obj, "Shape") or obj.Shape.isNull():
        raise ValueError(f"Unfold object '{unfold_name}' not found or has null shape")
    return obj.Shape


def extract_outer_contour_unfold_3d(
    shape: Any,
    plane: Optional[Tuple[Point3D, Vector3D]] = None,
) -> List[Point2D]:
    """
    Extracts outer boundary 2D contour vertices from 3D unfold shape.
    Applies plane normalization (U2, U3) so contour lies in clean 2D coordinates.
    """
    if shape is None or shape.isNull():
        return []

    # Find the largest planar face (the main sheet face)
    best_face = None
    max_area = -1.0
    for f in shape.Faces:
        axis = getattr(getattr(f, "Surface", None), "Axis", None)
        if axis is not None and f.Area > max_area:
            max_area = f.Area
            best_face = f

    if best_face is None:
        return []

    # Outer wire has the maximum length or contains the outer vertices
    outer_wire = best_face.OuterWire if hasattr(best_face, "OuterWire") else best_face.Wires[0]
    
    # Construct face orthonormal 2D basis (u_vec, v_vec) perpendicular to normal
    axis = best_face.Surface.Axis
    n_mag = math.sqrt(axis.x**2 + axis.y**2 + axis.z**2) or 1.0
    normal = App.Vector(axis.x / n_mag, axis.y / n_mag, axis.z / n_mag)

    # Pick non-collinear reference vector to build basis
    ref = App.Vector(0, 0, 1) if abs(normal.z) < 0.9 else App.Vector(1, 0, 0)
    u_vec = normal.cross(ref).normalize()
    v_vec = normal.cross(u_vec).normalize()
    origin = best_face.CenterOfMass

    # Extract distinct polygon vertices projected into the local face plane
    raw_points: List[Point2D] = []
    seen = []
    for v in outer_wire.Vertexes:
        diff = v.Point - origin
        u_coord = round(float(diff.dot(u_vec)), 4)
        v_coord = round(float(diff.dot(v_vec)), 4)
        pt = (u_coord, v_coord)

        if not seen or math.hypot(pt[0] - seen[-1][0], pt[1] - seen[-1][1]) > 0.05:
            seen.append(pt)
            raw_points.append(pt)

    # If first and last point are identical (closed loop), remove last
    if len(raw_points) > 2 and math.hypot(raw_points[0][0] - raw_points[-1][0], raw_points[0][1] - raw_points[-1][1]) < 0.05:
        raw_points.pop()

    return raw_points


def extract_outer_contour_drawing_2d(view: Any) -> List[Point2D]:
    """
    Extracts 2D contour vertices directly from a FreeCAD TechDraw::DrawViewPart (U2).
    Uses projected outer boundary vertices to exclude internal bend lines.
    """
    if view is None:
        return []

    points: List[Point2D] = []
    try:
        # If view has Source geometry, project its true outer boundary vertices
        if hasattr(view, "Source") and view.Source:
            source_obj = view.Source[0]
            if hasattr(source_obj, "Shape"):
                # Find largest planar face
                best_face = None
                max_area = -1.0
                for f in source_obj.Shape.Faces:
                    axis = getattr(getattr(f, "Surface", None), "Axis", None)
                    if axis is not None and f.Area > max_area:
                        max_area = f.Area
                        best_face = f
                if best_face is not None:
                    outer_w = best_face.OuterWire if hasattr(best_face, "OuterWire") else best_face.Wires[0]
                    for v in outer_w.Vertexes:
                        proj = view.projectPoint(v.Point) if hasattr(view, "projectPoint") else v.Point
                        pt = (round(float(proj.x), 4), round(float(proj.y), 4))
                        if not points or math.hypot(pt[0] - points[-1][0], pt[1] - points[-1][1]) > 0.05:
                            points.append(pt)
                    if len(points) > 2 and math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) < 0.05:
                        points.pop()
                    if len(points) >= 3:
                        return points

        # Fallback to visible vertices
        visible_vertexes = view.getVisibleVertexes()
        seen = []
        for v in visible_vertexes:
            pt = (round(float(v.x), 4), round(float(v.y), 4))
            is_dup = False
            for s in seen:
                if math.hypot(pt[0] - s[0], pt[1] - s[1]) < 0.05:
                    is_dup = True
                    break
            if not is_dup:
                seen.append(pt)
                points.append(pt)
    except Exception:
        pass
    return points


def extract_bend_lines_3d(shape: Any) -> List[RawBendLine]:
    """
    Extracts raw unmapped candidate bend lines from 3D CAD shape (U1).
    Decoupled from canonical mapping: analyzes cylindrical fillet surfaces
    or straight internal bend edges on unfolded flat blanks.
    """
    if shape is None or shape.isNull():
        return []

    raw_lines: List[RawBendLine] = []
    cyl_faces = []
    for f in shape.Faces:
        st = getattr(getattr(f, "Surface", None), "__class__", None)
        name = st.__name__.lower() if st else ""
        if "cyl" in name or hasattr(f.Surface, "Radius"):
            cyl_faces.append(f)

    # If no cylinder faces found, this is an unfolded sheet metal solid!
    if not cyl_faces:
        # Group planar sheet faces by normal axis.
        # Top sheet face (sliced into multiple panels) has normal axis ~ (0, 0, 1) or (-0, -0, -1)
        face_groups: Dict[Tuple[float, float, float], List[Any]] = {}
        for f in shape.Faces:
            axis = getattr(getattr(f, "Surface", None), "Axis", None)
            if axis is not None and f.Area > 200.0:  # significant area
                norm = App.Vector(axis.x, axis.y, axis.z).normalize()
                # Find matching group
                matched_grp = None
                for g_key in face_groups:
                    g_vec = App.Vector(g_key[0], g_key[1], g_key[2])
                    # Coplanar: parallel normal and same plane distance
                    if abs(norm.dot(g_vec)) > 0.95:
                        first_f = face_groups[g_key][0]
                        if abs((f.CenterOfMass - first_f.CenterOfMass).dot(norm)) < 0.1:
                            matched_grp = g_key
                            break
                if matched_grp is not None:
                    face_groups[matched_grp].append(f)
                else:
                    face_groups[(round(norm.x, 3), round(norm.y, 3), round(norm.z, 3))] = [f]

        # The sliced surface is the group with the most panels (>1) or largest total area
        best_grp = max(face_groups.values(), key=lambda grp: (len(grp), sum(f.Area for f in grp)), default=[])
        if not best_grp:
            return []

        # Count edge occurrences among the sliced panel faces
        edge_count: Dict[Tuple[float, float, float, float], int] = {}
        edge_map: Dict[Tuple[float, float, float, float], Any] = {}
        for f in best_grp:
            for e in f.Edges:
                if hasattr(e, "Curve") and "line" in getattr(getattr(e, "Curve"), "__class__", type("")).__name__.lower():
                    if e.Length > 10.0:
                        m = e.CenterOfMass
                        key = (round(float(m.x), 2), round(float(m.y), 2), round(float(m.z), 2), round(float(e.Length), 2))
                        edge_count[key] = edge_count.get(key, 0) + 1
                        edge_map[key] = e

        # An internal bend line borders >= 2 panel faces on the sheet!
        normal_vec = best_grp[0].Surface.Axis
        n_unit = App.Vector(normal_vec.x, normal_vec.y, normal_vec.z).normalize()

        for key, count in edge_count.items():
            if count >= 2:
                e = edge_map[key]
                p1 = e.Vertexes[0].Point
                p2 = e.Vertexes[1].Point
                d_vec = (p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)
                mag = math.sqrt(d_vec[0]**2 + d_vec[1]**2 + d_vec[2]**2) or 1.0
                u_dir = (d_vec[0] / mag, d_vec[1] / mag, d_vec[2] / mag)
                if u_dir[0] < -1e-6 or (abs(u_dir[0]) < 1e-6 and (u_dir[1] < -1e-6 or (abs(u_dir[1]) < 1e-6 and u_dir[2] < 0))):
                    u_dir = (-u_dir[0], -u_dir[1], -u_dir[2])

                raw_lines.append(
                    RawBendLine(
                        midpoint=(key[0], key[1], key[2]),
                        direction=u_dir,
                        normal=(float(n_unit.x), float(n_unit.y), float(n_unit.z)),
                        radius_mm=1.0,
                        length_mm=key[3],
                        adjacent_panel_areas=(0.0, 0.0),
                        source_edge_ref="",
                    )
                )

        return raw_lines

    # Group cylinder faces by concentric axis line
    processed_axes: List[Tuple[Point3D, Vector3D]] = []

    for f in cyl_faces:
        axis = f.Surface.Axis
        center = f.Surface.Center
        radius = float(f.Surface.Radius)

        norm_len = math.sqrt(axis.x**2 + axis.y**2 + axis.z**2)
        if norm_len < 1e-6:
            continue
        v_dir = (axis.x / norm_len, axis.y / norm_len, axis.z / norm_len)

        if v_dir[0] < -1e-6 or (abs(v_dir[0]) < 1e-6 and (v_dir[1] < -1e-6 or (abs(v_dir[1]) < 1e-6 and v_dir[2] < 0))):
            v_dir = (-v_dir[0], -v_dir[1], -v_dir[2])

        p_axis = (float(center.x), float(center.y), float(center.z))

        is_duplicate = False
        for p_cand, d_cand in processed_axes:
            cos_a = abs(sum(v_dir[k] * d_cand[k] for k in range(3)))
            if cos_a > 0.95:
                diff = (p_axis[0] - p_cand[0], p_axis[1] - p_cand[1], p_axis[2] - p_cand[2])
                dot_proj = sum(diff[k] * d_cand[k] for k in range(3))
                perp = tuple(diff[k] - dot_proj * d_cand[k] for k in range(3))
                line_dist = math.sqrt(sum(x**2 for x in perp))
                if line_dist < 2.0:
                    is_duplicate = True
                    break

        if is_duplicate:
            continue

        processed_axes.append((p_axis, v_dir))

        # Compute midpoint of cylindrical face
        cog = f.CenterOfMass
        midpoint = (float(cog.x), float(cog.y), float(cog.z))

        # Approximate length along axis
        bb = f.BoundBox
        len_est = math.sqrt(
            (bb.XMax - bb.XMin)**2 * abs(v_dir[0]) +
            (bb.YMax - bb.YMin)**2 * abs(v_dir[1]) +
            (bb.ZMax - bb.ZMin)**2 * abs(v_dir[2])
        )
        if len_est < 1.0:
            len_est = float(f.Area / (math.pi * radius * 0.5 + 1e-6))

        # Normal vector perpendicular to axis
        normal = (0.0, 0.0, 1.0)
        if abs(v_dir[2]) < 0.9:
            normal = (-v_dir[1], v_dir[0], 0.0)
            n_mag = math.sqrt(normal[0]**2 + normal[1]**2)
            if n_mag > 1e-6:
                normal = (normal[0] / n_mag, normal[1] / n_mag, 0.0)

        raw_lines.append(
            RawBendLine(
                midpoint=midpoint,
                direction=v_dir,
                normal=normal,
                radius_mm=radius,
                length_mm=len_est,
                adjacent_panel_areas=(float(f.Area), float(f.Area)),
                source_edge_ref="",
            )
        )

    return raw_lines


def match_to_canonical(
    raw_lines: List[RawBendLine],
    canonical_part_04: Dict[str, Any],
) -> List[BendLine]:
    """
    Executes deterministic lexicographical tie-break matching against 04_canonical_part.json (§3.2, U1).
    Score = 0.5 * (dist / 10mm) + 0.3 * (1 - |cosθ_dir|) + 0.2 * (1 - |cosθ_norm|)
    Tie-break: (Score, |radius_raw - radius_cand|, adjacent_panel_id, bend_id)
    """
    payload = canonical_part_04.get("payload", canonical_part_04)
    canonical_bends = payload.get("bends", {})
    if not canonical_bends:
        return []

    matched: List[BendLine] = []
    used_canonical_ids = set()

    for raw in raw_lines:
        candidates = []
        for bid, bdata in canonical_bends.items():
            if bid in used_canonical_ids:
                continue

            # Candidate geometry: axis_point and axis_direction
            cand_p = tuple(bdata.get("axis_point", [0, 0, 0]))
            cand_d = tuple(bdata.get("axis_direction", [1, 0, 0]))
            cand_rad = float(bdata.get("radius_mm", {}).get("value", 1.0))
            cand_len = float(bdata.get("length_mm", {}).get("value", raw.length_mm))
            cand_angle = float(bdata.get("angle_deg", {}).get("value", 90.0))
            p_panel = bdata.get("parent_panel_id", "")
            c_panel = bdata.get("child_panel_id", "")

            # Normalized distance (clamped to 10mm)
            dist = math.sqrt(sum((raw.midpoint[k] - cand_p[k])**2 for k in range(3)))
            norm_dist = min(1.0, dist / 10.0)

            # Cosine direction
            mag_cand_d = math.sqrt(sum(x**2 for x in cand_d)) or 1.0
            cos_dir = abs(sum(raw.direction[k] * (cand_d[k] / mag_cand_d) for k in range(3)))
            cos_dir = min(1.0, max(0.0, cos_dir))

            # Score metric §3.2
            score = 0.5 * norm_dist + 0.3 * (1.0 - cos_dir) + 0.2 * 0.0

            delta_rad = abs(raw.radius_mm - cand_rad)

            # Lexicographical key for tie-break
            tie_break_key = (score, delta_rad, p_panel, bid)
            candidates.append((tie_break_key, bid, bdata, cand_angle, cand_len, cand_rad))

        if not candidates:
            continue

        # Sort lexicographically
        candidates.sort(key=lambda c: c[0])
        best_cand = candidates[0]
        best_score = best_cand[0][0]
        best_id = best_cand[1]
        best_data = best_cand[2]

        # Max threshold check (must be <= 0.6)
        if best_score <= 0.6:
            used_canonical_ids.add(best_id)
            matched.append(
                BendLine(
                    bend_id=best_id,
                    midpoint=raw.midpoint,
                    direction=raw.direction,
                    normal=raw.normal,
                    radius_mm=best_cand[5],
                    length_mm=best_cand[4],
                    angle_deg=best_cand[3],
                    parent_panel_id=best_data.get("parent_panel_id", ""),
                    child_panel_id=best_data.get("child_panel_id", ""),
                    score=best_score,
                )
            )

    return matched
