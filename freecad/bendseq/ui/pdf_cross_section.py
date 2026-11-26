"""True plane cuts of existing PDF preview faces (s, machine X, z).

This is a visualization of the recorded geometry, not a collision solver.
The half-open edge rule preserves concave boundaries and holes. Only
closed contours are filled; open boundaries remain visibly unfilled.
"""
from collections import defaultdict
from math import isfinite
from freecad.bendseq.ui.pdf_export_options import face_layer, visible_faces

_COLORS = dict(part="#476389", punch="#ab6855", die="#598b7c",
               gauge="#d58a39", frame="#a1a9b5", clamps="#9aa6b9",
               collision="#d51e26")


def section_x(hit, overlap_faces=()):
    """Prefer the bounds midpoint, but ensure a nonempty exact mesh cut."""
    bounds = hit.get("overlap_bbox_xyz_mm") or {}
    x = 0.0
    try:
        lo, hi = float(bounds["xmin"]), float(bounds["xmax"])
        if isfinite(lo) and isfinite(hi) and lo <= hi:
            x = lo + (hi-lo)/2
    except (KeyError, ValueError, TypeError):
        pass
    if not overlap_faces or any(face_segments(face,x) for face in overlap_faces):
        return x
    # A disconnected exact intersection can have an empty central cut.
    # Choose the midpoint of the widest actual intersectable face.
    candidates = []
    for face in overlap_faces:
        depths = [v[1] for v in face.vertices]
        if depths:
            lo,hi = min(depths),max(depths)
            if hi>lo:
                mid = lo+(hi-lo)/2
                if face_segments(face,mid):
                    candidates.append((hi-lo,mid))
    return max(candidates)[1] if candidates else x


def face_segments(face, x):
    """Intersect one planar polygon with X=x, respecting interior rings."""
    points = []
    for ring in (face.vertices, *face.holes):
        for a, b in zip(ring, (*ring[1:], ring[0])) if ring else ():
            if (a[1] <= x < b[1]) or (b[1] <= x < a[1]):
                t = (x-a[1])/(b[1]-a[1])
                points.append((a[0]+t*(b[0]-a[0]), a[2]+t*(b[2]-a[2])))
    if len(points) < 2 or len(points) % 2:
        return ()
    dimension = max(range(2), key=lambda i: max(p[i] for p in points)-min(p[i] for p in points))
    points.sort(key=lambda p: (p[dimension],p[1-dimension]))
    return tuple((a,b) for a,b in zip(points[::2],points[1::2]) if a != b)


def contours(segments, tol=1e-5):
    """Weld matching endpoints and trace only unambiguous closed loops."""
    positions, adjacency, edges = {}, defaultdict(set), set()
    for a,b in segments:
        ka,kb = (tuple(round(v/tol) for v in p) for p in (a,b))
        if ka == kb:
            continue
        positions.setdefault(ka,a)
        positions.setdefault(kb,b)
        edge = tuple(sorted((ka,kb)))
        if edge in edges:
            continue
        edges.add(edge)
        adjacency[ka].add(kb)
        adjacency[kb].add(ka)
    result = []
    while edges:
        start = next((v for v in adjacency if len(adjacency[v]) != 2
                      and any(tuple(sorted((v,n))) in edges for n in adjacency[v])), None)
        if start is None:
            start = min(edges)[0]
        path = [start]
        current = start
        while True:
            candidates = sorted(n for n in adjacency[current] if tuple(sorted((current,n))) in edges)
            if not candidates:
                break
            nxt = candidates[0]
            edges.remove(tuple(sorted((current,nxt))))
            path.append(nxt)
            current = nxt
            if current == start or len(adjacency[current]) != 2:
                break
        result.append((tuple(positions[k] for k in path), current == start and len(path) >= 4))
    return tuple(result)


def section_contours(faces, x, options):
    groups = defaultdict(list)
    for face in visible_faces(faces, options):
        layer = face_layer(face)
        if face.kind.endswith("_seam"):
            continue
        conditional = face.kind == "collision_conditional"
        group = (layer,face.identifier,face.section_index if layer in ("punch","die") else 0,conditional)
        groups[group].extend(face_segments(face,x))
    return tuple((key,contours(segments)) for key,segments in groups.items() if segments)


def draw_section(painter, core, gui, faces, x, caption, evidence, *, options, left, width):
    groups = section_contours(faces,x,options)
    painter.save()
    try:
        painter.setClipRect(left,0,width,270)
        painter.fillRect(core.QRectF(left,0,width,270),gui.QColor("#ffffff"))
        painter.setPen(gui.QColor("#34465a"))
        painter.drawText(core.QRectF(left+4,3,width-8,18),core.Qt.AlignCenter,f"2D section · X={x:+.2f} mm")
        painter.drawText(core.QRectF(left+4,22,width-8,20),core.Qt.AlignCenter,caption)
        if any(face_layer(f) == "collision" for f in faces) and not any(
                key[0] == "collision" for key,_ in groups):
            evidence = "No overlap cut at this plane; see recorded evidence"
        flags = core.Qt.AlignCenter
        wrap = core.Qt.TextWordWrap
        # PySide6 scoped enums expose .value; PySide2 flags are int-like.
        flags = int(getattr(flags,"value",flags)) | int(getattr(wrap,"value",wrap))
        painter.drawText(core.QRectF(left+4,226,width-8,42),flags,evidence)
        points = [p for (layer,_,_,_),paths in groups
                  if options.fit_machine or layer in ("part","collision","gauge")
                  for path,_ in paths for p in path]
        if not points:
            points = [p for _,paths in groups for path,_ in paths for p in path]
        if not points:
            painter.drawText(core.QRectF(left+4,50,width-8,170),core.Qt.AlignCenter,"Section unavailable")
            return
        xs,zs = zip(*points)
        sx,sz = (min(xs)+max(xs))/2,(min(zs)+max(zs))/2
        scale = min((width-22)/max(1,max(xs)-min(xs)),170/max(1,max(zs)-min(zs)))
        def point(p):
            return core.QPointF(left+width/2+(p[0]-sx)*scale,135-(p[1]-sz)*scale)
        painter.setClipRect(left+2,44,width-4,180)
        for (layer,_,_,conditional),paths in sorted(groups,key=lambda g:g[0][0]=="collision"):
            color = gui.QColor("#e89127" if conditional else _COLORS[layer])
            ink = gui.QColor(color)
            ink.setAlpha(options.alpha(layer))
            fill = gui.QColor(color)
            fill.setAlpha(round(options.alpha(layer)*0.45) if layer!="collision" else options.alpha(layer))
            painter.setPen(gui.QPen(ink,2.1 if layer=="collision" else 1.25))
            closed = gui.QPainterPath()
            closed.setFillRule(core.Qt.OddEvenFill)
            opened = gui.QPainterPath()
            for path,is_closed in paths:
                qpath = closed if is_closed else opened
                qpath.moveTo(point(path[0]))
                for p in path[1:]:
                    qpath.lineTo(point(p))
                if is_closed:
                    qpath.closeSubpath()
            painter.setBrush(fill)
            painter.drawPath(closed)
            painter.setBrush(core.Qt.NoBrush)
            painter.drawPath(opened)
    finally:
        painter.restore()
