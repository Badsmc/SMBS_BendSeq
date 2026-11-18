"""Whole-sheet, live 3D preview geometry from authoritative 2.5D FoldState.

Returns actual transformed panel surfaces and finite-radius bend-zone strips
in machine (s, depth, z) coordinates. The consumer may project these faces
through an isometric camera. No FreeCAD Shape is modified and no second
angle solver is used. This is surface preview, not a BRep collision engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from functools import cache


@dataclass(frozen=True)
class PreviewFace3D:
    vertices: tuple
    kind: str
    identifier: str
    holes: tuple = ()
    section_index: int = 0


class PreviewMeshError(ValueError):
    pass


class _IdentityPlacement:
    """World XYZ -> unchanged world XYZ (used for fold-mesh reuse)."""

    def apply(self, point):
        return point


IDENTITY_PLACEMENT = _IdentityPlacement()


def transform_preview_faces(faces, machine_placement):
    """Apply one rigid placement to an already-folded sheet mesh.

    Input geometry uses preview axes (-world Y, world X, world Z). Reconstruct
    world XYZ, apply the authoritative machine placement exactly once, then
    return preview axes (-machine Y, machine X, machine Z).

    This allows APPROACH, RETRACT and TRANSFER to reuse local folded faces
    while moving the entire workpiece. No geometry solver or OCC calls.
    """
    if machine_placement is None:
        raise PreviewMeshError("machine placement is required")

    @cache
    def relocate(point):
        s, depth, z = point
        machine = machine_placement.apply(
            (float(depth), -float(s), float(z))
        )
        out = (-float(machine[1]),
               float(machine[0]),
               float(machine[2]))
        if not all(isfinite(value) for value in out):
            raise PreviewMeshError("nonfinite transformed preview vertex")
        return out

    return tuple(PreviewFace3D(
        tuple(relocate(v) for v in face.vertices),
        face.kind, face.identifier,
        tuple(tuple(relocate(v) for v in hole) for hole in face.holes),
    ) for face in faces)


def build_tool_preview_faces(tooling, *, arc_segments=20,
                             punch_ram_z_mm=None, preview_punch_length_mm=None,
                             active_bend_length_mm=None, section_preview=None):
    """Extrude the selected 2D machine profiles along the fixed machine X axis.

    The 2D viewer and press cycle own both profile transforms. In particular,
    only set_punch_ram_z changes the punch's machine Z position. Coordinates
    here use the sheet preview convention (s=-Y_m, depth=X_m, z=Z_m).
    """
    if not tooling.visible:
        return ()
    punch_delta = 0.0
    if punch_ram_z_mm is not None:
        punch_delta = float(punch_ram_z_mm) - float(tooling.ram_z_mm)
        if not isfinite(punch_delta):
            raise PreviewMeshError("nonfinite inspected punch position")
    if preview_punch_length_mm is not None:
        length_override = float(preview_punch_length_mm)
        if not isfinite(length_override) or length_override <= 0.0:
            raise PreviewMeshError("invalid display-only punch length")
    else:
        length_override = None
    bend_length = (tooling.active_bend_length_mm if active_bend_length_mm is None
                   else active_bend_length_mm)
    faces = []
    for kind, profile, polygons, offsets in (
        ("punch", tooling.punch, tooling.punch_polygons_machine,
         tooling.punch_section_offsets_xm),
        ("die", tooling.die, tooling.die_polygons_machine,
         tooling.die_section_offsets_xm),
    ):
        if profile is None:
            continue
        spans = (section_preview or {}).get(kind) or getattr(tooling, f"{kind}_section_spans_xm", ())
        if not spans:
            length = (length_override if kind == "punch" and length_override is not None
                      else bend_length if tooling.match_active_bend_length else profile.length_mm)
            if (isinstance(length, bool) or not isinstance(length, (int, float))
                    or not isfinite(length) or length <= 0):
                raise PreviewMeshError(kind + " has no verified preview length")
            spans = tuple((offset - length / 2.0, offset + length / 2.0)
                          for offset in (offsets or (0.0,)))
        outline, holes = polygons(arc_segments=arc_segments)
        if kind == "punch" and punch_delta:
            outline = tuple((s, z + punch_delta) for s, z in outline)
            holes = tuple(tuple((s, z + punch_delta) for s, z in hole)
                          for hole in holes)
        if len(outline) < 3:
            raise PreviewMeshError(kind + " has no usable machine profile")
        rings = (outline,) + tuple(holes)
        if any(not all(isfinite(s) and isfinite(z) for s, z in ring)
               for ring in rings):
            raise PreviewMeshError(kind + " has nonfinite machine coordinates")
        for section_index, (near, far) in enumerate(spans):
            if not isfinite(near) or not isfinite(far):
                raise PreviewMeshError(kind + " has nonfinite machine station")

            def at_depth(ring, depth):
                return tuple((float(s), depth, float(z)) for s, z in ring)

            for at_start, depth in ((True, near), (False, far)):
                neighbor = section_index - 1 if at_start else section_index + 1
                if 0 <= neighbor < len(spans):
                    edge = spans[neighbor][1 if at_start else 0]
                    if abs(edge - depth) <= 1e-6:
                        if at_start:
                            faces.append(PreviewFace3D(
                                at_depth((*outline, outline[0]), depth),
                                kind + "_seam", profile.tool_id,
                            ))
                        continue
                faces.append(PreviewFace3D(
                    at_depth(outline, depth), kind + "_cap",
                    profile.tool_id,
                    tuple(at_depth(hole, depth) for hole in holes),
                    section_index,
                ))
            for ring in rings:
                for index, point in enumerate(ring):
                    following = ring[(index + 1) % len(ring)]
                    if point == following:
                        continue
                    s0, z0 = point
                    s1, z1 = following
                    faces.append(PreviewFace3D(
                        ((s0, near, z0), (s1, near, z1),
                         (s1, far, z1), (s0, far, z0)),
                        kind + "_side", profile.tool_id, section_index=section_index,
                    ))
    return tuple(faces)


def build_sheet_preview_faces(
    fold_state, panel_vertices_xy, active_bend_id, pose, segments=12,
    *, machine_placement=None, panel_meshes=None,
):
    """Build real panel/bend faces; optionally project the SAME machine pose.

    machine_placement is a PlacementTransform3D from the press-cycle.
    All sheet points are first evaluated in the authoritative FoldState
    and then transformed into machine axes (s=-Y_m, depth=X_m, z=Z_m).
    Never apply the legacy WorkpiecePose2D on top of machine placement.
    """
    if active_bend_id not in fold_state.bends:
        raise PreviewMeshError("Unknown active bend")
    active = fold_state.bends[active_bend_id]
    ref = active.bend_zone
    if ref is None:
        raise PreviewMeshError("Active bend has no BendZone")
    ox, oy = ref.origin_xy
    ux, uy = ref.left_xy
    side = 1.0 if active.moving_left else -1.0
    ux *= side
    uy *= side
    dx, dy = ref.axis_dir_xy
    if abs(ux * dx + uy * dy) > 1e-6:
        raise PreviewMeshError("Invalid cross-section frame")

    def world_to_machine(p):
        x, y, z = (float(v) for v in p)
        if machine_placement is IDENTITY_PLACEMENT:
            result = (-y, x, z)
        elif machine_placement is not None:
            machine_point = machine_placement.apply((x, y, z))
            result = (
                -float(machine_point[1]),
                float(machine_point[0]),
                float(machine_point[2]),
            )
        else:
            s = (x - ox) * ux + (y - oy) * uy
            depth = (x - ox) * dx + (y - oy) * dy
            sp, zp = pose.apply((s, z))
            result = (sp, depth, zp)
        if not all(isfinite(v) for v in result):
            raise PreviewMeshError("Nonfinite projected sheet coordinates")
        return result

    faces = []
    thickness = float(ref.thickness_mm)
    for panel_id in fold_state.graph.panels:
        pts_xy = panel_vertices_xy.get(panel_id)
        if not pts_xy or len(pts_xy) < 3:
            continue
        tf = fold_state.transforms[panel_id]
        (a, b, c), (d, e, f), (g, h, i) = tf.rotation
        tx, ty, tz = tf.translation

        @cache
        def project(point, z):
            x, y = point
            return world_to_machine((a*x + b*y + c*z + tx,
                                     d*x + e*y + f*z + ty,
                                     g*x + h*y + i*z + tz))

        def polygon(points, z):
            return tuple(project(point, z) for point in points)

        caps, rings = (panel_meshes or {}).get(panel_id, ((pts_xy,), (pts_xy,)))
        for cap in caps:
            top = polygon(cap, 0.0)
            bottom = polygon(cap, -thickness)
            faces.append(PreviewFace3D(top, "panel_top", str(panel_id)))
            faces.append(PreviewFace3D(tuple(reversed(bottom)), "panel_bottom", str(panel_id)))
        for ring in rings:
            top = polygon(ring, 0.0)
            bottom = polygon(ring, -thickness)
            for i in range(len(top)):
                j = (i + 1) % len(top)
                if hypot(top[i][0]-top[j][0], top[i][1]-top[j][1]) < 1e-7:
                    continue
                faces.append(PreviewFace3D(
                    (top[i], top[j], bottom[j], bottom[i]), "panel_edge", str(panel_id)
                ))

    for bend_id, bend in fold_state.bends.items():
        zone = bend.bend_zone
        if zone is None:
            continue
        parent_tf = fold_state.transforms[bend.fixed_panel]
        section = fold_state.zone_section(bend_id, segments=segments)
        zone_side = 1.0 if bend.moving_left else -1.0
        zx, zy = zone.left_xy[0] * zone_side, zone.left_xy[1] * zone_side
        ends = zone.axis_xy

        def strip_at_end(profile, end):
            ex, ey = end
            return tuple(world_to_machine(parent_tf.point(
                (ex + zx * s, ey + zy * s, z)
            )) for s, z in profile)

        for surface in ("inner", "outer"):
            p1 = strip_at_end(section[surface], ends[0])
            p2 = strip_at_end(section[surface], ends[1])
            for i in range(min(len(p1),len(p2)) - 1):
                faces.append(PreviewFace3D(
                    (p1[i], p1[i+1], p2[i+1], p2[i]),
                    "zone_" + surface, str(bend_id),
                ))
        for end in ends:
            p1 = strip_at_end(section["inner"], end)
            p2 = strip_at_end(section["outer"], end)
            for i in range(min(len(p1),len(p2)) - 1):
                faces.append(PreviewFace3D(
                    (p1[i], p1[i+1], p2[i+1], p2[i]),
                    "zone_end", str(bend_id),
                ))

    if not faces:
        raise PreviewMeshError("No drawable panels or bend-zone surfaces")
    return tuple(faces)
