"""On-demand exact OCC intersection inspector for Manual native D6 collisions.

This module never validates a safe sequence. It reconstructs the SAME
sampled machine pose from the native D6 report, checks actual FreeCAD shapes
and opens an isolated, UNSAVED FreeCAD document for visual inspection.
No geometry from the user's original FCStd is modified or persisted.
"""
from __future__ import annotations

from math import dist, isfinite
from pathlib import Path
from typing import Any


class NativeCollisionInspectionError(ValueError):
    """Cannot reconstruct the authoritative sampled collision without guessing."""


def _validated_collision_pose(bend_id: str, blocker: dict) -> dict:
    """Fail closed on missing or contradictory native sample coordinates."""
    if not isinstance(blocker, dict):
        raise NativeCollisionInspectionError("missing_native_collision_report")
    if str(blocker.get("tool", "")) not in ("PUNCH", "DIE", "PUNCH_AND_DIE"):
        raise NativeCollisionInspectionError("no_confirmed_sheet_tool_collision")
    pose = blocker.get("sampled_machine_pose") or {}
    if str(pose.get("bend_id")) != str(bend_id):
        raise NativeCollisionInspectionError("native_collision_bend_id_mismatch")
    if str(pose.get("phase")) != str(blocker.get("phase")):
        raise NativeCollisionInspectionError("native_collision_phase_mismatch")
    rotation = pose.get("rotation")
    translation = pose.get("translation")
    if (not isinstance(rotation, (list, tuple)) or len(rotation) != 3
            or not isinstance(translation, (list, tuple))
            or len(translation) != 3):
        raise NativeCollisionInspectionError("missing_native_machine_placement")
    try:
        values = [float(v) for row in rotation for v in row]
        values.extend(float(v) for v in translation)
        values.extend([
            float(pose["ram_z_mm"]),
            float(pose["signed_angle_deg"]),
        ])
    except (ValueError, TypeError, KeyError) as exc:
        raise NativeCollisionInspectionError("invalid_native_machine_placement") from exc
    if len(values) != 14 or not all(isfinite(v) for v in values):
        raise NativeCollisionInspectionError("invalid_native_machine_placement")
    if (blocker.get("angle_deg") is not None and abs(
            float(blocker["angle_deg"]) - float(pose["signed_angle_deg"])
    ) > 1e-4):
        raise NativeCollisionInspectionError("native_collision_angle_mismatch")
    return pose


def _common_positive(sheet, tool, *, volume_tol_mm3=1e-4):
    """Use the native OCC boolean result, never bounding box intersection."""
    from .native_collision_3d import check_solid_validity, measured_common
    for label, shape in (("workpiece", sheet), ("tool", tool)):
        valid, reason = check_solid_validity(shape, label)
        if not valid:
            raise NativeCollisionInspectionError("invalid_{}:{}".format(label, reason))
    try:
        exact = measured_common(sheet, tool, label="collision_inspector_common")
        if (exact is None or exact.isNull() or not exact.isValid()
                or not isfinite(float(exact.Volume))):
            raise ValueError("invalid_occ_common")
        vol = float(exact.Volume)
    except Exception as exc:
        raise NativeCollisionInspectionError(
            "occ_common_failed:{}:{}".format(type(exc).__name__, exc)
        ) from exc
    return (exact, vol) if vol > volume_tol_mm3 else (None, vol)


def _check_replay_volume(blocker: dict, tool_kind: str, actual_mm3: float) -> None:
    key = ("first_sample_punch_overlap_mm3" if tool_kind == "PUNCH"
           else "first_sample_die_overlap_mm3")
    expected = blocker.get(key)
    if expected is None:
        raise NativeCollisionInspectionError("missing_sampled_volume:" + tool_kind)
    expected = float(expected)
    if not isfinite(expected) or expected < 0:
        raise NativeCollisionInspectionError("invalid_sampled_volume:" + tool_kind)
    # The two computations both use the same FCStd, bend geometry and native
    # machine pose. A material difference is evidence of a stale setup,
    # ambiguous geometry, or a broken replay, NOT evidence of safe contact.
    if abs(expected - actual_mm3) > max(0.05, expected * 0.02):
        raise NativeCollisionInspectionError(
            "native_replay_overlap_mismatch:{}:logged={:.6f}:actual={:.6f}"
            .format(tool_kind, expected, actual_mm3)
        )


def reconstruct_exact_collision(
    session: Any,
    tooling: Any,
    bend_id: str,
    blocker: dict,
    *,
    segments: int = 128,
) -> dict:
    """Recreate exact native D6 sheet/tool OCC Common at the logged pose.

    Returns real FreeCAD Part.Shapes for the sheet, tools, full OCC common and
    per-panel/bend-zone OCC common. Does not create GUI documents or mutate the
    original FoldState. A mismatch or failed native boolean raises clearly.
    """
    pose = _validated_collision_pose(bend_id, blocker)
    import FreeCAD as App
    from .panel_kinematics import FoldState
    from .workpiece_placement_3d import PlacementTransform3D
    from .native_collision_attribution import _machine_transform
    from .native_bend_solid_3d import (
        _apply_rigid_transform, _source_panel_prism,
        build_multi_bend_sheet_solid, build_bend_zone_solid,
    )
    from .native_tool_loader import load_native_punch, load_native_die, set_punch_ram_z
    from .physical_validation_25d import _source_path

    source = session.fold_state
    state = FoldState(source.graph, source.fixed_panel)
    if bend_id not in state.bends:
        raise NativeCollisionInspectionError("bend_not_in_loaded_part")
    for bid, angle in (pose.get("completed_angles") or {}).items():
        if bid not in state.bends or bid == bend_id:
            raise NativeCollisionInspectionError("invalid_completed_bend:" + str(bid))
        state.set_angle(bid, float(angle))
    state.set_angle(bend_id, float(pose["signed_angle_deg"]))
    machine = PlacementTransform3D(
        rotation=tuple(tuple(float(x) for x in row) for row in pose["rotation"]),
        translation=tuple(float(x) for x in pose["translation"]),
    )
    punch_src = _source_path(getattr(tooling, "punch", None))
    die_src = _source_path(getattr(tooling, "die", None))
    if punch_src is None or die_src is None:
        raise NativeCollisionInspectionError("selected_verified_FCStd_tool_missing")
    if (getattr(tooling, "punch_section_offsets_xm", ())
            or getattr(tooling, "die_section_offsets_xm", ())):
        if getattr(tooling, "match_active_bend_length", False):
            raise NativeCollisionInspectionError("sections_incompatible_with_virtual_length")
    virtual_len = (dist(*state.bends[bend_id].axis_xyz)
                   if bool(getattr(tooling, "match_active_bend_length", False)) else None)

    # Build exactly as the authoritative D6 service, using source SheetMetal
    # panel faces and selected FCStd tool profiles, never a 2D proxy.
    sheet = build_multi_bend_sheet_solid(
        state, session.panel_build, segments=int(segments),
    ).shape
    sheet_machine = _machine_transform(sheet, machine, App)
    punch = load_native_punch(
        Path(punch_src), virtual_length_mm=virtual_len,
        section_offsets_xm=getattr(tooling, "punch_section_offsets_xm", ()) or None,
    )
    punch_machine = set_punch_ram_z(
        punch, float(pose["ram_z_mm"]),
    ).machine_shape
    die_machine = load_native_die(
        Path(die_src), virtual_length_mm=virtual_len,
        section_offsets_xm=getattr(tooling, "die_section_offsets_xm", ()) or None,
    ).machine_shape
    tools = {"PUNCH": punch_machine, "DIE": die_machine}
    requested = (["PUNCH", "DIE"] if blocker["tool"] == "PUNCH_AND_DIE"
                 else [blocker["tool"]])
    common_shapes = {}
    volumes = {}
    for tool_kind in requested:
        exact, vol = _common_positive(sheet_machine, tools[tool_kind])
        _check_replay_volume(blocker, tool_kind, vol)
        if exact is None:
            raise NativeCollisionInspectionError(
                "native_replay_lost_confirmed_collision:" + tool_kind
            )
        common_shapes[tool_kind] = exact
        volumes[tool_kind] = vol

    named = []
    attribution = blocker.get("component_attribution") or {}
    hit_list = attribution.get("hits") or ()
    relevant_ids = {str(hit.get("component_id")) for hit in hit_list}
    if hit_list:
        first = state.ordered_bends[0].bend_zone
        if first is None:
            raise NativeCollisionInspectionError("missing_sheet_thickness")
        t = float(first.thickness_mm)
        panels = dict(
            (panel.panel_id, face)
            for panel, face in zip(
                session.panel_build.panels, session.panel_build.panel_faces
            )
        )
        for hit in hit_list:
            name = str(hit.get("component_id", ""))
            kind = str(hit.get("tool", "")).upper()
            if name not in relevant_ids or kind not in tools:
                raise NativeCollisionInspectionError("invalid_attributed_component")
            if name.startswith("panel:"):
                panel_id = name.split(":", 1)[1]
                if panel_id not in panels:
                    raise NativeCollisionInspectionError("missing_attributed_panel:" + name)
                base = _source_panel_prism(panel_id, panels[panel_id], t, App)
                source_component = _apply_rigid_transform(
                    base, state.transforms[panel_id], App,
                )
            elif name.startswith("bend_zone:"):
                zone_id = name.split(":", 1)[1]
                if zone_id not in state.bends:
                    raise NativeCollisionInspectionError("missing_attributed_zone:" + name)
                source_component = build_bend_zone_solid(
                    state, zone_id, segments=int(segments),
                ).shape
            else:
                raise NativeCollisionInspectionError("unknown_component:" + name)
            component = _machine_transform(source_component, machine, App)
            piece, volume = _common_positive(component, tools[kind])
            expected_volume = float(hit.get("overlap_volume_mm3", 0.0))
            if abs(volume - expected_volume) > max(0.05, expected_volume * 0.02):
                raise NativeCollisionInspectionError(
                    "attributed_overlap_mismatch:{}:{:.6f}:{:.6f}"
                    .format(name, expected_volume, volume)
                )
            if piece is not None:
                named.append({
                    "component_id": name, "tool": kind,
                    "shape": piece, "component_shape": component,
                    "volume_mm3": volume,
                })
    return {
        "sheet": sheet_machine,
        "punch": punch_machine,
        "die": die_machine,
        "common": common_shapes,
        "volumes_mm3": volumes,
        "components": named,
        "pose": pose,
        "attribution_status": attribution.get("status", "NOT_RECORDED"),
        "attribution_complete": bool(attribution.get("complete", False)),
        "virtual_length": virtual_len is not None,
    }


def open_exact_collision_document(
    session: Any, tooling: Any, bend_id: str, blocker: dict,
) -> dict:
    """Open a NEW unsaved native FreeCAD document with editable tree visibility.

    The original FCStd remains untouched. Shape objects only: no source CAD
    modifications, no document.save, and no false clearance certification.
    """
    from uuid import uuid4
    shapes = reconstruct_exact_collision(session, tooling, bend_id, blocker)
    import FreeCAD as App
    import FreeCADGui as Gui

    name = "SMBS_OCC_{}_{}".format(bend_id, uuid4().hex[:8])
    previous_doc = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    doc = App.newDocument(name)
    try:
        context = doc.addObject("App::DocumentObjectGroup", "MachineScene")
        context.Label = "Машинная установка — контекст (прозрачный)"
        contacts = doc.addObject("App::DocumentObjectGroup", "ExactCollisions")
        contacts.Label = "ТОЧНЫЕ пересечения OCC — красные тела"

        def add(group, object_name, label, shape, rgb, transparency=0):
            obj = doc.addObject("Part::Feature", object_name)
            obj.Label = label
            obj.Shape = shape.copy()
            group.addObject(obj)
            obj.ViewObject.ShapeColor = rgb
            obj.ViewObject.LineColor = (0.16, 0.16, 0.17)
            obj.ViewObject.Transparency = transparency
            obj.ViewObject.LineWidth = 1.5
            try:
                obj.ViewObject.DisplayMode = "Flat Lines"
            except (AttributeError, ValueError):
                pass
            return obj

        add(context, "Sheet", "Деталь — заданный угол", shapes["sheet"],
            (0.55, 0.68, 0.77), 68)
        add(context, "Punch", "Пуансон — действительная форма FCStd",
            shapes["punch"], (0.19, 0.49, 0.85), 75)
        add(context, "Die", "Матрица — действительная форма FCStd",
            shapes["die"], (0.20, 0.68, 0.46), 70)
        contact_objects = []
        for kind, solid in shapes["common"].items():
            label = "КОЛЛИЗИЯ: деталь × {} — {:.6f} мм³".format(
                "пуансон" if kind == "PUNCH" else "матрица",
                shapes["volumes_mm3"][kind],
            )
            contact_objects.append(add(
                contacts, "Contact" + kind, label, solid,
                (1.0, 0.07, 0.07), 0,
            ))
        for i, entry in enumerate(shapes["components"]):
            tag = entry["component_id"]
            label = "{} × {} — {:.6f} мм³".format(
                tag, entry["tool"], entry["volume_mm3"],
            )
            obj = add(
                contacts, "ExactComponent{:02d}".format(i), label,
                entry["shape"], (1.0, 0.3, 0.05), 0,
            )
            # Avoid z-fighting with red full intersection solids by default.
            obj.Visibility = False

        doc.recompute()
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        for obj in contact_objects:
            Gui.Selection.addSelection(doc.Name, obj.Name)
        return {
            "document_name": doc.Name,
            "bend_id": str(bend_id),
            "phase": shapes["pose"].get("phase"),
            "angle_deg": shapes["pose"]["signed_angle_deg"],
            "volumes_mm3": shapes["volumes_mm3"],
            "components": [
                {"name": c["component_id"], "tool": c["tool"],
                 "volume_mm3": c["volume_mm3"]}
                for c in shapes["components"]
            ],
            "attribution_status": shapes["attribution_status"],
            "attribution_complete": shapes["attribution_complete"],
            "virtual_length": shapes["virtual_length"],
            "source_document_unchanged": True,
            "saved": False,
        }
    except Exception:
        App.closeDocument(doc.Name)
        if previous_doc:
            try:
                App.setActiveDocument(previous_doc)
            except (AttributeError, ValueError):
                pass
        raise
