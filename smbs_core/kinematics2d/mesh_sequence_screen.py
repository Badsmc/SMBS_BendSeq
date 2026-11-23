"""Time-budgeted triangle surface screen for the press-cycle poses."""
from copy import copy
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from .press_cycle import CyclePhase, PressCycleController
from .preview_mesh_3d import build_sheet_preview_faces


class MeshBudgetExpired(Exception):
    pass


def _bounds(points):
    return tuple((min(axis), max(axis)) for axis in zip(*points))


def _overlaps(a, b):
    return all(lo <= end and start <= hi for (lo, hi), (start, end) in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    x, y, z = a
    u, v, w = b
    return y*w-z*v, z*u-x*w, x*v-y*u


def _pierces(start, end, triangle):
    """Transverse crossing of the triangle interior by the open segment."""
    a, b, c = triangle
    ab, ac = _sub(b, a), _sub(c, a)
    direction = _sub(end, start)
    h = _cross(direction, ac)
    det = _dot(ab, h)
    if abs(det) <= 1e-10:
        return False
    offset = _sub(start, a)
    u = _dot(offset, h) / det
    q = _cross(offset, ab)
    v = _dot(direction, q) / det
    t = _dot(ac, q) / det
    eps = 1e-7
    return eps < t < 1-eps and u > eps and v > eps and u+v < 1-eps


@dataclass(frozen=True)
class _Node:
    bounds: tuple
    entries: tuple = ()
    children: tuple = ()


def _tree(entries, check_budget):
    check_budget()
    bounds = tuple(
        (min(box[i][0] for box, _ in entries), max(box[i][1] for box, _ in entries))
        for i in range(3)
    )
    if len(entries) <= 8:
        return _Node(bounds, tuple(entries))
    axis = max(range(3), key=lambda i: bounds[i][1] - bounds[i][0])
    ordered = sorted(entries, key=lambda entry: sum(entry[0][axis]))
    mid = len(ordered) // 2
    return _Node(bounds, children=(
        _tree(ordered[:mid], check_budget), _tree(ordered[mid:], check_budget),
    ))


def _crosses_tool(triangle, tree, ram_z, check_budget):
    # Only the punch translates. Query its original mesh in local coordinates.
    triangle = tuple((x, y, z-ram_z) for x, y, z in triangle)
    bounds = _bounds(triangle)
    stack = [tree]
    while stack:
        check_budget()
        node = stack.pop()
        if not _overlaps(bounds, node.bounds):
            continue
        stack.extend(node.children)
        for other_bounds, other in node.entries:
            if not _overlaps(bounds, other_bounds):
                continue
            for edges, target in ((triangle, other), (other, triangle)):
                if any(_pierces(edges[i-1], point, target) for i, point in enumerate(edges)):
                    return True
    return False


def native_tool_meshes(tooling, virtual_length, check_budget=lambda: None):
    """Load the same machine-space CAD mesh for collision screening and display.

    The punch mesh is at ram Z=0. Both consumers apply the saved ram height
    as a translation, with no modification of the die.
    """
    from .native_tool_loader import load_native_die, load_native_punch
    from .physical_validation_25d import _source_path
    from .tooling_assembly import native_section_options

    for kind, load in (("punch", load_native_punch), ("die", load_native_die)):
        check_budget()
        path = _source_path(getattr(tooling, kind))
        if path is None:
            raise ValueError("Нет проверенного FCStd для " + kind)
        tool = load(path, virtual_length_mm=virtual_length, **native_section_options(tooling, kind))
        check_budget()
        vertices, facets = tool.machine_shape.tessellate(0.1)
        check_budget()
        points = [(float(v.x), float(v.y), float(v.z)) for v in vertices]
        if not facets or not all(isfinite(c) for p in points for c in p):
            raise ValueError("Непригодная сетка оснастки: " + kind)
        yield kind, tool, tuple(tuple(points[i] for i in facet) for facet in facets)


def _native_tool_trees(tooling, virtual_length, check_budget):
    return {
        kind: (tool, _tree([(_bounds(t), t) for t in triangles], check_budget))
        for kind, tool, triangles in native_tool_meshes(tooling, virtual_length, check_budget)
    }


def _panel_meshes(session, state, check_budget):
    """Tessellate rigid CAD panels once, preserving their through holes."""
    panel_build = getattr(session, "panel_build", None)
    faces = getattr(panel_build, "panel_faces", ())
    panels = getattr(panel_build, "panels", ())
    if not faces or not panels:
        return {}
    import FreeCAD as App
    from .native_bend_solid_3d import _source_panel_prism

    thickness = state.ordered_bends[0].bend_zone.thickness_mm
    meshes = {}
    for panel, face in zip(panels, faces):
        check_budget()
        solid = _source_panel_prism(panel.panel_id, face, thickness, App)
        vertices, facets = solid.tessellate(0.1)
        check_budget()
        if not facets:
            raise ValueError("Пустая сетка панели " + panel.panel_id)
        points = tuple((float(v.x), float(v.y), float(v.z)) for v in vertices)
        meshes[panel.panel_id] = (points, facets)
    if set(meshes) != set(state.graph.panels):
        raise ValueError("Геометрия панелей Mesh не совпадает с деталью")
    return meshes


def _sheet_triangles(session, cycle, panel_meshes, placement):
    from freecad_smbs.ui.tool_cap_triangulation import triangulate_tool_cap

    state = cycle.sequence.fold_state
    for panel_id, (points, facets) in panel_meshes.items():
        transform = state.transforms[panel_id]
        placed = tuple(placement.apply(transform.point(p)) for p in points)
        for facet in facets:
            yield panel_id, tuple(placed[i] for i in facet)

    faces = build_sheet_preview_faces(
        state, {} if panel_meshes else session.panel_vertices_xy,
        cycle.active_bend_id, None,
        machine_placement=placement,
        panel_meshes=getattr(session.unfold_data, "panel_meshes", None),
    )
    for face in faces:
        vertices = tuple((depth, -s, z) for s, depth, z in face.vertices)
        if len(vertices) <= 4 and face.kind not in ("panel_top", "panel_bottom"):
            indices = ((0, i, i+1) for i in range(1, len(vertices)-1))
        elif len(vertices) == 3:
            indices = ((0, 1, 2),)
        else:
            normal = max(
                (_cross(_sub(vertices[i], vertices[0]), _sub(vertices[i+1], vertices[0]))
                 for i in range(1, len(vertices)-1)),
                key=lambda v: _dot(v, v),
            )
            axis = max(range(3), key=lambda i: abs(normal[i]))
            axes = [i for i in range(3) if i != axis]
            indices = triangulate_tool_cap(
                tuple((p[axes[0]], 0., p[axes[1]]) for p in vertices)
            )
        for triple in indices:
            yield face.identifier, tuple(vertices[i] for i in triple)


def _confirm_crossing(session, state, component, placement, tool_shape, prisms, check_budget, end_zone_length=None, min_overlap_mm3=1e-4):
    """Measure common volume only for the component flagged by Mesh."""
    import FreeCAD as App

    from .native_bend_solid_3d import (
        _apply_rigid_transform, _source_panel_prism, build_bend_zone_solid,
    )
    from .native_collision_3d import (
        _evaluate_common_volume, classify_punch_overlap_zones,
        PunchCollisionZoneClassification,
    )

    check_budget()
    if component in state.bends:
        shape = build_bend_zone_solid(state, component, segments=64).shape
        shape = _apply_rigid_transform(shape, placement, App)
    else:
        prism = prisms.get(component)
        if prism is None:
            panel_build = session.panel_build
            face = next(face for panel, face in zip(panel_build.panels, panel_build.panel_faces)
                        if panel.panel_id == component)
            thickness = state.ordered_bends[0].bend_zone.thickness_mm
            prism = _source_panel_prism(component, face, thickness, App, prisms)
        world_shape = _apply_rigid_transform(prism, state.transforms[component], App)
        shape = _apply_rigid_transform(world_shape, placement, App)
    check_budget()
    volume, bounds, common = _evaluate_common_volume(
        shape, tool_shape, include_bbox=True, include_shape=True,
    )
    conditional = False
    if end_zone_length is not None and volume > min_overlap_mm3:
        check_budget()
        zones = classify_punch_overlap_zones(
            shape, tool_shape, end_zone_length, _precomputed_common=common,
        )
        conditional = zones.classification is PunchCollisionZoneClassification.END_ZONE_ONLY
    return volume, bounds, conditional, shape, tool_shape, common


def _is_forming_contact_sliver(kind, phase, component, active_bend, volume, bounds, *, fraction):
    """Recognize only sub-micron forming contact on the active bend at the punch."""
    return (
        kind == "punch"
        and (phase is CyclePhase.BENDING
             or (phase is CyclePhase.RETRACT and fraction == 0.0))
        and component == active_bend
        and 0.0 < volume <= 0.001
        and bounds is not None
        and 0.0 <= bounds["zmax"] - bounds["zmin"] <= 0.001
    )


def screen_mesh_sequence(session, preview_cycle, order, *, budget_seconds=60.0, min_overlap_mm3=1e-4,
                         progress=None, on_collision=None):
    """Check sampled surfaces in the current operation order using CAD tool meshes."""
    start = perf_counter()

    def check_budget():
        if perf_counter() - start >= budget_seconds:
            raise MeshBudgetExpired

    sequence = copy(preview_cycle.sequence)
    sequence.fold_state = copy(sequence.fold_state)
    sequence.reset()
    tooling = copy(preview_cycle.tooling)
    tool_cache = {}
    finished, findings, unresolved = [], [], []
    unconfirmed_crossings = 0
    forming_contact_slivers = []
    conditional_findings = []
    panel_prisms = {}
    samples = 0
    reason = ""
    try:
        cycle = PressCycleController(sequence, tooling, config=preview_cycle.config)
        cycle.loading_orientation_by_bend = preview_cycle.loading_orientation_by_bend
        cycle.loading_rear_frame_for_step = getattr(preview_cycle, "loading_rear_frame_for_step", None)
        cycle.assisted_retract_preview_bends = set(preview_cycle.assisted_retract_preview_bends)
        panel_meshes = _panel_meshes(session, sequence.fold_state, check_budget)
        for index, operation_id in enumerate(order):
            check_budget()
            sequence.select_operation(index)
            bend_id = cycle.active_bend_id
            if operation_id != sequence.active_operation_id:
                raise ValueError("Порядок Mesh не совпадает с порядком симуляции")
            if progress is not None:
                progress(index, len(order), operation_id)
            cycle._sync_bounds_and_orientation_for_active_bend()
            bend = sequence.fold_state.bends[bend_id]
            points = bend.axis_xyz
            length = sum((a-b)**2 for a,b in zip(*points))**0.5
            virtual_length = length if tooling.match_active_bend_length else None
            if virtual_length not in tool_cache:
                tool_cache[virtual_length] = _native_tool_trees(tooling, virtual_length, check_budget)
            tools = tool_cache[virtual_length]
            start_angle = cycle.current_angle_deg
            target = cycle.target_angle_deg
            fractions = (0., .25, .5, .75, 1.)
            reported = set()
            conditional_reported = set()
            confirmed = {}
            last_pose = last_triangles = None
            for phase in (CyclePhase.APPROACH, CyclePhase.BENDING, CyclePhase.RETRACT):
                for fraction in fractions:
                    check_budget()
                    cycle.phase = phase
                    if phase is CyclePhase.APPROACH:
                        cycle.ram_z_mm = cycle.upper_z_mm + fraction*(cycle.contact_z_mm-cycle.upper_z_mm)
                    elif phase is CyclePhase.RETRACT:
                        cycle.ram_z_mm = cycle.bottom_z_mm + fraction*(cycle.upper_z_mm-cycle.bottom_z_mm)
                    else:
                        angle = start_angle + fraction*(target-start_angle)
                        sequence.fold_state.set_angle(bend_id, angle)
                        cycle.ram_z_mm = cycle.contact_z_mm - cycle.coupled_kinematics.stroke_for_angle(abs(angle))
                    placement = cycle.current_workpiece_placement_3d()
                    pose = (cycle.current_angle_deg, placement)
                    if pose != last_pose:
                        triangles = []
                        for item in _sheet_triangles(session, cycle, panel_meshes, placement):
                            check_budget()
                            triangles.append(item)
                        last_pose, last_triangles = pose, triangles
                    tested = set()
                    tool_shapes = {}
                    for component, triangle in last_triangles:
                        check_budget()
                        for kind, (tool, tree) in tools.items():
                            pair = kind, component
                            if pair in tested or pair in reported:
                                continue
                            ram_z = cycle.ram_z_mm if kind == "punch" else 0.0
                            if not _crosses_tool(triangle, tree, ram_z, check_budget):
                                continue
                            tested.add(pair)
                            key = pair, pose, ram_z
                            hit = {
                                "operation": operation_id, "bend": bend_id,
                                "phase": phase.value, "fraction": fraction,
                                "tool": kind, "component": component,
                                "ram_z_mm": cycle.ram_z_mm, "angle_deg": cycle.current_angle_deg,
                            }
                            try:
                                if key not in confirmed:
                                    if kind not in tool_shapes:
                                        from .native_tool_loader import set_punch_ram_z

                                        tool_shapes[kind] = (
                                            set_punch_ram_z(tool, ram_z).machine_shape
                                            if kind == "punch" else tool.machine_shape
                                        )
                                    confirmed[key] = _confirm_crossing(
                                        session, sequence.fold_state, component, placement,
                                        tool_shapes[kind], panel_prisms, check_budget,
                                        min_overlap_mm3=min_overlap_mm3,
                                        end_zone_length=(
                                            tooling.end_zone_length_mm
                                            if kind == "punch" and tooling.allow_end_zone_collision
                                            else None
                                        ),
                                    )
                                    check_budget()
                                volume, bounds, conditional, component_shape, tool_shape, common = confirmed[key]
                            except MeshBudgetExpired:
                                raise
                            except Exception as exc:
                                unresolved.append({**hit, "reason": str(exc)})
                                continue
                            if volume > min_overlap_mm3 and _is_forming_contact_sliver(
                                kind, phase, component, bend_id, volume, bounds, fraction=fraction,
                            ):
                                forming_contact_slivers.append({
                                    **hit, "occ_overlap_volume_mm3": volume,
                                    "overlap_bbox_xyz_mm": bounds,
                                })
                            elif volume > min_overlap_mm3:
                                result = {
                                    **hit,
                                    "occ_overlap_volume_mm3": volume,
                                    "overlap_bbox_xyz_mm": bounds,
                                    "sampled_machine_pose": {
                                        "angles_deg": dict(sequence.fold_state.angles),
                                        "rotation": placement.rotation,
                                        "translation": placement.translation,
                                    },
                                }
                                if conditional:
                                    if pair not in conditional_reported:
                                        conditional_findings.append(result)
                                        conditional_reported.add(pair)
                                else:
                                    if on_collision is not None:
                                        on_collision(result, component_shape, tool_shape, common)
                                    findings.append(result)
                                    reported.add(pair)
                            else:
                                unconfirmed_crossings += 1
                    samples += 1
            finished.append(operation_id)
    except MeshBudgetExpired:
        reason = "TIME_BUDGET"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    return {
        "completed": finished, "pending": list(order[len(finished):]),
        "findings": findings, "unresolved": unresolved,
        "forming_contact_slivers": forming_contact_slivers,
        "conditional_findings": conditional_findings,
        "mesh_crossings_without_occ_penetration": unconfirmed_crossings,
        "samples": samples, "reason": reason,
        "elapsed_seconds": perf_counter()-start,
    }
