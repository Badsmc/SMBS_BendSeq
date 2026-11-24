"""Cached display groups derived from the exact native tooling meshes."""

PALETTES = {
    'punch': ((0.85, 0.36, 0.22), (0.97, 0.68, 0.28), (0.72, 0.30, 0.46)),
    'die': ((0.20, 0.49, 0.67), (0.28, 0.71, 0.65), (0.42, 0.42, 0.73)),
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


def segment_label(span):
    return f'{span[1] - span[0]:g} mm'
