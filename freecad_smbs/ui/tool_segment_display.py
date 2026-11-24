"""Cached display groups derived from the exact native tooling meshes."""

PALETTES = {
    'punch': ((0.85, 0.36, 0.22), (0.98, 0.82, 0.22), (0.52, 0.25, 0.66)),
    'die': ((0.20, 0.49, 0.67), (0.25, 0.78, 0.48), (0.60, 0.34, 0.80)),
}


def segment_color(kind, index):
    colors = PALETTES[kind]
    return colors[index % len(colors)]


def native_display_sections(tool, triangles):
    """Partition existing facets by axial section; never tessellate or alter solids."""
    sections = tuple(tool.sections)
    if not sections:
        return ((0, tool.span_xm, triangles),)
    groups = [[] for _ in sections]
    for triangle in triangles:
        x = sum(v[0] for v in triangle) / 3
        # Facets belong to individual extruded sections, including their end caps.
        index = min(range(len(sections)), key=lambda i: max(
            sections[i].span_xm[0] - x, x - sections[i].span_xm[1], 0))
        groups[index].append(triangle)
    return tuple((i, section.span_xm, tuple(group))
                 for i, (section, group) in enumerate(zip(sections, groups)) if group)


def section_boundary_edges(span, triangles):
    """Exterior edges of both section end caps, without triangulation diagonals."""
    from collections import Counter
    counts = Counter()
    for triangle in triangles:
        if not any(all(abs(v[0] - x) < 1e-5 for v in triangle) for x in span):
            continue
        for a, b in zip(triangle, (*triangle[1:], triangle[0])):
            counts[tuple(sorted((tuple(round(c, 6) for c in a),
                                 tuple(round(c, 6) for c in b))))] += 1
    return tuple(edge for edge, count in counts.items() if count == 1)


def suggested_sections(workspace, bend_length):
    """Cache the stock planner's visual proposal; never apply a physical assembly."""
    from pathlib import Path
    from smbs_core.kinematics2d.segment_assembly_planner import plan_pair_for_bend
    from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError

    setup = workspace.tooling_setup
    if setup.tool_assembly is not None:
        return None
    root = Path(workspace.tool_library.library_root)
    paths = [root / 'config.json']
    for kind in ('punch', 'die'):
        tool = getattr(setup, kind)
        if tool is None:
            return None
        paths.append(Path(tool.source_file).with_suffix('.tooling.json'))
    stamps = tuple((str(p), p.stat().st_mtime_ns if p.exists() else None) for p in paths)
    machine = getattr(getattr(workspace, '_preview_3d', None), '_machine', None)
    clamp_spans = tuple(getattr(machine, 'clamp_spans', ()))
    key = (id(setup.punch), id(setup.die), stamps,
           repr(getattr(workspace, '_project_clamp_config', None)), clamp_spans)
    cache = getattr(workspace, '_segment_display_cache', None)
    if cache is None or cache[0] != key:
        cache = (key, {})
        workspace._segment_display_cache = cache
    plans = cache[1]
    if bend_length not in plans:
        try:
            from dataclasses import replace
            punch, die, config = workspace._tool_assembly_inputs(require_pair=False)
            if not config.centres_mm and clamp_spans:
                config = replace(config, positions_mm=tuple((a+b)/2 for a,b in clamp_spans))
            plan = plan_pair_for_bend(punch, die, bend_length, config)
            punch_plan = plan.get('punch_preview', plan)
            sections = {}
            for kind, source in (('punch', punch_plan), ('die', plan)):
                spans = tuple((item['start_x_mm'], item['end_x_mm'])
                              for item in source.get(kind + '_segments', ()))
                if spans:
                    sections[kind] = spans
            plans[bend_length] = sections or None
        except (OSError, ValueError, RuntimeError, ClampValidatorError):
            plans[bend_length] = None
    return plans[bend_length]
