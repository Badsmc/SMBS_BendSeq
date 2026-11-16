"""3D preview-mesh face AABB risk estimator for axis-staged search.

This is deliberately broadphase only. An overlapping face AABB is POSSIBLE
interference, not a collision; no candidate is rejected. Uses the same
FoldState and tooling profiles as FreeCAD 2.5D, without mutating originals.
There is no claim of full swept-volume/continuous 3D clearance.
"""
from __future__ import annotations

from copy import copy
from math import dist, isfinite
from time import perf_counter


def face_bbox(face):
    points = tuple(face.vertices)
    if len(points) < 3:
        raise ValueError("face has fewer than three vertices")
    xyz = tuple(tuple(float(p[i]) for i in range(3)) for p in points)
    if not all(isfinite(x) for p in xyz for x in p):
        raise ValueError("non-finite mesh vertex")
    return tuple((min(p[i] for p in xyz), max(p[i] for p in xyz))
                 for i in range(3))


def boxes_intersect(a, b, *, tolerance_mm=0.02):
    """Overlap is possible; touching within tolerance is not proved clear."""
    return all(x[1] + tolerance_mm >= y[0] and
               y[1] + tolerance_mm >= x[0]
               for x, y in zip(a, b))


def mesh_face_aabb_risk(sheet_faces, tool_faces, *, tolerance_mm=0.02):
    """Count tool/sheet face-bounding overlaps with component attribution.

    Works on real 3D preview vertices. Concave faces and holes are NOT
    triangulated, so this remains a conservative per-face broadphase
    rather than an exact mesh intersection test.
    """
    sheets = tuple((face, face_bbox(face)) for face in sheet_faces)
    tools = tuple((face, face_bbox(face)) for face in tool_faces)
    if not sheets or not tools:
        raise ValueError("missing sheet or tool preview geometry")
    pairs = set()
    for sf, sb in sheets:
        for tf, tb in tools:
            if boxes_intersect(sb, tb, tolerance_mm=tolerance_mm):
                pairs.add((str(sf.identifier), str(tf.kind)))
    return {
        "possible_overlap_pairs": len(pairs),
        "possible_components": tuple(sorted(pairs)),
        "sheet_faces": len(sheets),
        "tool_faces": len(tools),
    }


def preview_mesh_aabb_risk(
    session, tooling, sequence, orientations, *,
    match_active_bend_length=True, deadline=None,
    sample_fractions=(0.0, 0.5, 1.0),
    tolerance_mm=0.02,
):
    """Replay a full order at sampled 3D preview poses; rank, never reject.

    Workpiece and tool faces share (s, machine X, Z) coordinates. The tool
    geometry is preview extrusion of selected profiles, NOT native FCStd
    BRep. Does not assert machine feasibility, even if no AABB overlaps.
    """
    from .coupled_kinematics_2d import CoupledAirBendingKinematics
    from .machine_section_3d import compute_machine_cutting_plane_section
    from .preview_mesh_3d import (
        build_sheet_preview_faces, build_tool_preview_faces,
    )
    from .workpiece_placement_3d import WorkpiecePlacement3D

    state = session.fold_state
    vertices = session.panel_vertices_xy
    original_angles = dict(state.angles)
    original_transforms = dict(state.transforms)
    local_tool = copy(tooling)
    local_tool.visible = True
    possible_samples = 0
    checks = 0
    sampled_pairs = set()
    completed = []
    try:
        for bid in sequence:
            if deadline is not None and perf_counter() >= deadline:
                return {
                    "status": "UNVERIFIED",
                    "reason": "BROADPHASE_TIME_LIMIT",
                    "sampled_bends": len(completed),
                    "possible_overlap_samples": possible_samples,
                }
            state.reset_all()
            for previous in completed:
                state.set_angle(previous, float(state.bends[previous].angle_deg))
            bend = state.bends[bid]
            zone = bend.bend_zone
            if zone is None:
                raise ValueError("missing native bend zone at " + bid)
            a, b = bend.axis_xyz
            length = dist(a, b)
            if match_active_bend_length:
                local_tool.set_match_active_bend_length(True, length)
            else:
                local_tool.set_match_active_bend_length(False)
            outer = float(zone.outer_radius_mm)
            reverse = bool(orientations.get(bid, False))
            initial_pose = WorkpiecePlacement3D.compute_placement(
                state, bid, support_plane_zm=outer,
                align_bisector=True, align_bend_arc_center=True,
                reverse_axis=reverse,
            )
            initial_section = compute_machine_cutting_plane_section(
                state, vertices, initial_pose,
                active_bend_id=bid, segments=24,
            )
            local_tool.align_to_cross_section(
                initial_section, clearance_mm=5.0, bend_id=bid,
            )
            coupled = CoupledAirBendingKinematics.from_tooling_and_fold_state(
                local_tool, state, bid,
            )
            for fraction in sample_fractions:
                if deadline is not None and perf_counter() >= deadline:
                    return {
                        "status": "UNVERIFIED",
                        "reason": "BROADPHASE_TIME_LIMIT",
                        "sampled_bends": len(completed),
                        "possible_overlap_samples": possible_samples,
                    }
                angle = float(bend.angle_deg) * float(fraction)
                state.set_angle(bid, angle)
                stroke = coupled.stroke_for_angle(abs(angle))
                pose = WorkpiecePlacement3D.compute_placement(
                    state, bid,
                    support_plane_zm=(outer if fraction == 0 else outer-stroke),
                    align_bisector=True, align_bend_arc_center=True,
                    reverse_axis=reverse,
                )
                ram_z = coupled.ram_z_for_angle(abs(angle))
                local_tool.set_punch_ram_z(
                    ram_z, stroke_mm=max(0.0, coupled.contact_z_mm-ram_z),
                )
                sheet_faces = build_sheet_preview_faces(
                    state, vertices, bid, None,
                    machine_placement=pose, segments=8,
                )
                tool_faces = build_tool_preview_faces(
                    local_tool, arc_segments=12,
                )
                contact = mesh_face_aabb_risk(
                    sheet_faces, tool_faces, tolerance_mm=tolerance_mm,
                )
                if contact["possible_overlap_pairs"]:
                    possible_samples += 1
                    for component, kind in contact["possible_components"]:
                        sampled_pairs.add((str(bid), component, kind))
                checks += 1
            completed.append(bid)
        return {
            "status": ("SAMPLED_PREVIEW_BBOX_POSSIBLE_OVERLAP"
                       if possible_samples else "SAMPLED_PREVIEW_BBOX_SEPARATED"),
            "possible_overlap_samples": possible_samples,
            "possible_pairs": [list(x) for x in sorted(sampled_pairs)[:40]],
            "sampled_bends": len(completed),
            "samples": checks,
            "meaning": "PREVIEW_SURFACE_AABB_RISK_ONLY_NOT_NATIVE_COLLISION_PROOF",
        }
    except Exception as exc:
        return {
            "status": "UNVERIFIED",
            "reason": type(exc).__name__ + ":" + str(exc),
            "sampled_bends": len(completed),
            "possible_overlap_samples": possible_samples,
        }
    finally:
        state.angles = original_angles
        state.transforms = original_transforms
