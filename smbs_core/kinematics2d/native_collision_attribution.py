"""Optional, strictly post-collision FreeCAD OCC component attribution.

The authoritative full-sheet collision oracle is NOT replaced by this
diagnostic. Only after a confirmed positive-volume collision, decompose
the SAME panel/zone geometry at the reported native machine pose and test
which named components meet the selected physical tool. No substitutes,
empty fakes, or claims of verified clearance on partial diagnostic runs.
"""
from __future__ import annotations

from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any


def _machine_transform(shape: Any, transform: Any, App: Any):
    """Workpiece and tool are compared in the identical MACHINE frame."""
    matrix = App.Matrix()
    for i in range(3):
        for j in range(3):
            setattr(matrix, "A{}{}".format(i + 1, j + 1),
                    float(transform.rotation[i][j]))
        setattr(matrix, "A{}4".format(i + 1),
                float(transform.translation[i]))
    out = shape.copy()
    result = out.transformShape(matrix, True)
    return result if result is not None else out


def component_hits(
    components,
    tool_shape,
    *,
    tool_kind="PUNCH",
    max_checks=26,
    max_time_s=30.0,
    check_pair=None,
    get_gap=None,
):
    """Inspect named FreeCAD solids with cheap AABB before any OCC Common.

    Return *positive* overlaps, plus incomplete diagnostics if anything
    times out, fails or cannot be evaluated. An empty hit list with
    incomplete status is NOT proof that no component intersects.
    """
    if check_pair is None or get_gap is None:
        from .native_collision_3d import check_pair_clearance, compute_aabb_gap
        check_pair = check_pair or check_pair_clearance
        get_gap = get_gap or compute_aabb_gap
    started = perf_counter()
    hits, errors = [], []
    inspected = 0
    for name, solid in components:
        if inspected >= max_checks or perf_counter() - started >= max_time_s:
            errors.append("diagnostic_limit_reached")
            break
        inspected += 1
        try:
            gap = get_gap(solid.BoundBox, tool_shape.BoundBox)
            if gap is not None and gap > 0.0:
                continue
            result = check_pair(solid, tool_shape, tool_label=tool_kind)
            status = str(getattr(getattr(result, "status", None), "value",
                                 getattr(result, "status", "")))
            if status == "PENETRATION":
                hits.append({
                    "component_id": str(name),
                    "tool": str(tool_kind),
                    "overlap_volume_mm3": float(result.overlap_volume_mm3),
                    "overlap_bbox_xyz_mm": getattr(
                        result, "overlap_bbox_xyz_mm", None,
                    ),
                    "reason": str(getattr(result, "reason", "")),
                })
            elif status not in ("CLEAR", "TOUCHING", "NEAR_CONTACT"):
                errors.append("{}:{}".format(name, status))
        except Exception as exc:
            errors.append("{}:{}:{}".format(
                name, type(exc).__name__, str(exc)[:240],
            ))
    hits.sort(key=lambda entry: entry["overlap_volume_mm3"], reverse=True)
    return {
        "status": "COMPLETE" if not errors else "PARTIAL",
        "tool": str(tool_kind),
        "checked_components": inspected,
        "total_components": len(components),
        "positive_overlap_components": hits,
        "errors": errors,
        "elapsed_s": round(perf_counter() - started, 4),
        "clearance_certification": False,
    }


def collision_preview_boxes(blocker: dict) -> list[dict]:
    """Return honest native-XYZ bounding boxes for an OCC collision preview.

    Prefer complete per-component positive-common evidence when available.
    The full-sheet common AABB is only a fallback, never a contact surface.
    If even one attributed hit lacks a bounding box, fall back as a group:
    drawing only some positives would falsely suggest complete localization.
    """
    attribution = blocker.get("component_attribution") or {}
    hits = attribution.get("hits") or []
    if (attribution.get("status") == "ATTRIBUTED"
            and attribution.get("complete") is True
            and hits
            and all(isinstance(hit.get("overlap_bbox_xyz_mm"), dict)
                    for hit in hits)):
        return [
            {
                "tool": str(hit.get("tool", "UNKNOWN")),
                "component_id": str(hit.get("component_id", "UNKNOWN")),
                "bbox": hit["overlap_bbox_xyz_mm"],
                "source": "NATIVE_COMPONENT_COMMON_AABB",
            }
            for hit in hits
        ]
    return [
        {
            "tool": str(kind).upper(),
            "component_id": "whole_sheet",
            "bbox": bound,
            "source": "NATIVE_FULL_SHEET_COMMON_AABB",
        }
        for kind, bound in (
            blocker.get("intersection_bbox_xyz_mm") or {}
        ).items()
        if isinstance(bound, dict)
    ]


def attribute_native_collision(
    session: Any,
    collision_state: Any,
    *,
    punch_source: Path,
    die_source: Path,
    virtual_length: bool,
    bend_length_mm: float,
    tooling: Any,
    segments: int = 128,
    max_time_s: float = 30.0,
) -> dict:
    """Reconstruct individual source panels and bend zones at *exact* hit pose.

    All probes preserve the current FoldState and source FCStd document.
    The tool and geometry are real native solids. Failure produces an
    explicit UNVERIFIED result rather than guessing P09 from an X length.
    """
    start = perf_counter()
    record = {
        "status": "UNVERIFIED",
        "active_bend": str(collision_state.active_bend_id),
        "collision_phase": str(collision_state.phase),
        "collision_angle_deg": float(collision_state.signed_angle_deg),
        "collision_ram_z_mm": float(collision_state.ram_z_mm),
        "hits": [],
        "errors": [],
        "checked_component_count": 0,
        "complete": False,
        "derived_from": "AUTHORITATIVE_FOLDSTATE_COMPONENTS_AT_NATIVE_MACHINE_POSE",
    }
    try:
        import FreeCAD as App
        from .native_bend_solid_3d import (
            _apply_rigid_transform,
            _source_panel_prism,
            build_bend_zone_solid,
        )
        from .native_collision_3d import CollisionClass
        from .native_tool_loader import (
            load_native_punch, load_native_die, set_punch_ram_z,
        )
        from .panel_kinematics import FoldState

        source_state = session.fold_state
        independent = FoldState(source_state.graph, source_state.fixed_panel)
        # IMPORTANT: set target angle AND all completed bends without
        # changing the live session, in the same machine-space placement
        # as the native D6 collision state.
        for bend_id, angle in collision_state.completed_angles.items():
            independent.set_angle(bend_id, float(angle))
        independent.set_angle(
            collision_state.active_bend_id,
            collision_state.signed_angle_deg,
        )
        src_faces = session.panel_build.panel_faces
        src_panels = session.panel_build.panels
        zone = independent.ordered_bends[0].bend_zone
        thickness_mm = float(zone.thickness_mm)
        components = []
        if len(src_panels) != len(src_faces) or not src_panels:
            raise ValueError("inconsistent panel geometry source count")
        for panel, face in zip(src_panels, src_faces):
            if perf_counter() - start >= max_time_s:
                record["errors"].append("component_build_time_limit")
                break
            pshape = _source_panel_prism(
                panel.panel_id, face, thickness_mm, App,
            )
            pshape = _apply_rigid_transform(
                pshape, independent.transforms[panel.panel_id], App,
            )
            components.append((
                "panel:" + panel.panel_id,
                _machine_transform(
                    pshape, collision_state.workpiece_placement, App,
                ),
            ))
        if not record["errors"]:
            for bend in independent.ordered_bends:
                if perf_counter() - start >= max_time_s:
                    record["errors"].append("bend_zone_build_time_limit")
                    break
                zshape = build_bend_zone_solid(
                    independent, bend.bend_id, segments=segments,
                ).shape
                components.append((
                    "bend_zone:" + bend.bend_id,
                    _machine_transform(
                        zshape, collision_state.workpiece_placement, App,
                    ),
                ))

        common = collision_state.collision_report
        target_kinds = []
        for kind, result in (
            ("PUNCH", common.punch_result),
            ("DIE", common.die_result),
        ):
            status = getattr(result, "status", None)
            if status is CollisionClass.PENETRATION:
                target_kinds.append(kind)
        if not target_kinds:
            record["errors"].append("no_confirmed_native_tool_penetration")
            return record
        requested_length = float(bend_length_mm) if virtual_length else None
        p_sections = getattr(tooling, "punch_section_offsets_xm", ()) or ()
        d_sections = getattr(tooling, "die_section_offsets_xm", ()) or ()
        tools = {}
        if "PUNCH" in target_kinds:
            punch = load_native_punch(
                punch_source, virtual_length_mm=requested_length,
                section_offsets_xm=p_sections or None,
            )
            tools["PUNCH"] = set_punch_ram_z(
                punch, collision_state.ram_z_mm,
            ).machine_shape
        if "DIE" in target_kinds:
            tools["DIE"] = load_native_die(
                die_source, virtual_length_mm=requested_length,
                section_offsets_xm=d_sections or None,
            ).machine_shape
        for i, (kind, tool_shape) in enumerate(tools.items()):
            left_s = max_time_s - (perf_counter() - start)
            if left_s <= 0:
                record["errors"].append("component_attribution_timeout")
                break
            diag = component_hits(
                components, tool_shape, tool_kind=kind,
                max_checks=len(components), max_time_s=left_s,
            )
            record["hits"].extend(diag["positive_overlap_components"])
            record["checked_component_count"] += diag["checked_components"]
            record["errors"].extend(diag["errors"])
        record["hits"].sort(
            key=lambda v: v["overlap_volume_mm3"], reverse=True,
        )
        record["complete"] = (
            not record["errors"]
            and record["checked_component_count"]
            == len(components) * len(target_kinds)
        )
        record["status"] = (
            "ATTRIBUTED" if record["hits"] else "UNRESOLVED"
        )
        if not record["complete"]:
            record["status"] = "PARTIAL_" + record["status"]
    except Exception as exc:
        record["errors"].append(
            "{}:{}".format(type(exc).__name__, str(exc)[:500])
        )
    record["elapsed_s"] = round(perf_counter() - start, 4)
    return record
