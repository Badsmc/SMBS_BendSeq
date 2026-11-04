"""Whole-sheet, live 3D preview geometry from authoritative 2.5D FoldState.

Returns actual transformed panel surfaces and finite-radius bend-zone strips
in machine (s, depth, z) coordinates. The consumer may project these faces
through an isometric camera. No FreeCAD Shape is modified and no second
angle solver is used. This is surface preview, not a BRep collision engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite


@dataclass(frozen=True)
class PreviewFace3D:
    vertices: tuple
    kind: str
    identifier: str


class PreviewMeshError(ValueError):
    pass


def build_sheet_preview_faces(fold_state, panel_vertices_xy, active_bend_id, pose, segments=12):
    """Build top/bottom/side sheet faces plus every actual bend-zone surface."""
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
        s = (x - ox) * ux + (y - oy) * uy
        depth = (x - ox) * dx + (y - oy) * dy
        sp, zp = pose.apply((s, z))
        if not all(isfinite(v) for v in (sp, depth, zp)):
            raise PreviewMeshError("Nonfinite projected sheet coordinates")
        return (sp, depth, zp)

    faces = []
    thickness = float(ref.thickness_mm)
    for panel_id in fold_state.graph.panels:
        pts_xy = panel_vertices_xy.get(panel_id)
        if not pts_xy or len(pts_xy) < 3:
            continue
        tf = fold_state.transforms[panel_id]
        top = tuple(world_to_machine(tf.point((x, y, 0.0))) for x, y in pts_xy)
        bottom = tuple(world_to_machine(tf.point((x, y, -thickness))) for x, y in pts_xy)
        faces.append(PreviewFace3D(top, "panel_top", str(panel_id)))
        faces.append(PreviewFace3D(tuple(reversed(bottom)), "panel_bottom", str(panel_id)))
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
