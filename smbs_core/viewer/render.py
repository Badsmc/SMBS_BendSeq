"""
render.py - Offscreen PNG renderer via HLR + PIL (Stage 4, S.15a).

HLR = Hidden Line Removal. Visible edges solid, hidden edges dashed.
Honest marking (C2): red banner "PREVIEW_ONLY (KL-12) - not a physical re-simulation" on every frame.
No 3D viewport, no X display. Batch-only.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, List, Optional, Tuple

RENDER_KIND_HLR = "HLR"
RENDER_KIND_BBOX_FALLBACK = "BBOX_WIREFRAME"
BANNER_TEXT = "PREVIEW_ONLY (KL-12) - not a physical re-simulation"
BANNER_TEXT_VALID = "VALID_PER_PANEL (KL-12) - verified solid"


class RenderError(Exception):
    """Raised when rendering cannot proceed (no shape, HLR failure without fallback, PIL failure)."""


def _discretize_edges(shape: Any, deflection: float = 0.1) -> Tuple[List[List[Tuple[float, float]]], int]:
    """
    Discretize 2D projected curves of a TopoDS_Shape into list of coordinate segments [(x, y), ...].
    Returns (segments, skipped_count).
    """
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection

    segments: List[List[Tuple[float, float]]] = []
    skipped = 0
    if shape is None or shape.IsNull():
        return segments, skipped

    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = exp.Current()
        curve = BRepAdaptor_Curve(edge)
        try:
            discr = GCPnts_QuasiUniformDeflection(curve, deflection)
            if discr.IsDone() and discr.NbPoints() >= 2:
                pts = [(discr.Value(i).X(), discr.Value(i).Y()) for i in range(1, discr.NbPoints() + 1)]
                segments.append(pts)
            else:
                skipped += 1
        except Exception:
            skipped += 1
        exp.Next()
    return segments, skipped


def _render_hlr(
    shape: Any,
    view_dir: Tuple[float, float, float]
) -> Tuple[List[List[Tuple[float, float]]], List[List[Tuple[float, float]]], int, int]:
    """
    Execute HLR projection and return (visible_segments_2d, hidden_segments_2d, vis_skipped, hid_skipped).
    """
    from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCC.Core.HLRAlgo import HLRAlgo_Projector
    from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir

    hlr = HLRBRep_Algo()
    hlr.Add(shape)
    norm = math.sqrt(sum(v * v for v in view_dir))
    if norm < 1e-6:
        raise RenderError(f"View direction vector cannot be zero: {view_dir}")
    unit_dir = (view_dir[0] / norm, view_dir[1] / norm, view_dir[2] / norm)

    projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*unit_dir)))
    hlr.Projector(projector)
    hlr.Update()
    hlr.Hide()

    out = HLRBRep_HLRToShape(hlr)
    visible_shape = out.VCompound()
    hidden_shape = out.HCompound()

    vis_segs, vis_skipped = _discretize_edges(visible_shape)
    hid_segs, hid_skipped = _discretize_edges(hidden_shape)
    return vis_segs, hid_segs, vis_skipped, hid_skipped


def _project_point(p: Tuple[float, float, float], view_dir: Tuple[float, float, float]) -> Tuple[float, float]:
    """
    Project 3D point onto view plane orthogonal to view_dir.
    """
    norm = math.sqrt(sum(v * v for v in view_dir))
    vx, vy, vz = view_dir[0] / norm, view_dir[1] / norm, view_dir[2] / norm

    # Choose up-vector
    if abs(vx) < 1e-4 and abs(vy) < 1e-4:
        up = (0.0, 1.0, 0.0)
    else:
        up = (0.0, 0.0, 1.0)

    # Right vector = up x view_dir
    rx = up[1] * vz - up[2] * vy
    ry = up[2] * vx - up[0] * vz
    rz = up[0] * vy - up[1] * vx
    r_norm = math.sqrt(rx * rx + ry * ry + rz * rz)
    rx, ry, rz = rx / r_norm, ry / r_norm, rz / r_norm

    # True up vector = view_dir x right
    ux = vy * rz - vz * ry
    uy = vz * rx - vx * rz
    uz = vx * ry - vy * rx

    x_2d = p[0] * rx + p[1] * ry + p[2] * rz
    y_2d = p[0] * ux + p[1] * uy + p[2] * uz
    return x_2d, y_2d


def _render_bbox_fallback(
    shape: Any,
    view_dir: Tuple[float, float, float]
) -> List[List[Tuple[float, float]]]:
    """
    Extract 3D Bounding Box corners, wire 12 edges, and project to 2D view.
    """
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    if bbox.IsVoid():
        raise RenderError("Cannot extract bounding box from empty/void shape for fallback")

    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    corners = [
        (xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymax, zmin), (xmin, ymax, zmin),
        (xmin, ymin, zmax), (xmax, ymin, zmax), (xmax, ymax, zmax), (xmin, ymax, zmax)
    ]
    edges_idx = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7)
    ]

    proj_corners = [_project_point(c, view_dir) for c in corners]
    segments = [[proj_corners[i], proj_corners[j]] for i, j in edges_idx]
    return segments


def _draw_dashed_line(
    draw: Any,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    fill: str = "#BDBDBD",
    width: int = 1,
    dash: float = 6.0,
    gap: float = 4.0
) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    dist = math.hypot(dx, dy)
    if dist < 1e-4:
        return

    ux = dx / dist
    uy = dy / dist

    curr = 0.0
    while curr < dist:
        end = min(curr + dash, dist)
        start_pt = (x1 + ux * curr, y1 + uy * curr)
        end_pt = (x1 + ux * end, y1 + uy * end)
        draw.line([start_pt, end_pt], fill=fill, width=width)
        curr += dash + gap


def render_frame_png(
    shape: Any,
    output_path: Path,
    *,
    width: int = 800,
    height: int = 600,
    title: str,
    subtitle: str = "",
    view_dir: Tuple[float, float, float] = (1.0, 1.0, 1.0),
    geometry_precision: str = "PREVIEW_ONLY",
    geometry_warning: Optional[str] = None,
) -> str:
    """
    Render a single TopoDS_Shape to PNG via HLR + PIL.
    """
    if shape is None or not hasattr(shape, "IsNull") or shape.IsNull():
        raise RenderError("Cannot render frame: shape is None or IsNull()")

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RenderError(f"PIL (Pillow) is required for offscreen rendering: {e}") from e

    # 1. Attempt HLR rendering, fallback to BBOX_WIREFRAME if HLR fails
    render_kind = RENDER_KIND_HLR
    vis_segs: List[List[Tuple[float, float]]] = []
    hid_segs: List[List[Tuple[float, float]]] = []
    vis_skipped = 0
    hid_skipped = 0

    try:
        vis_segs, hid_segs, vis_skipped, hid_skipped = _render_hlr(shape, view_dir)
        if vis_skipped > 0:
            raise RenderError(f"{vis_skipped} visible edges failed to discretize (HLR partial)")
        if not vis_segs and not hid_segs:
            # If HLR gave 0 edges on a non-null shape, try bbox fallback
            vis_segs = _render_bbox_fallback(shape, view_dir)
            render_kind = RENDER_KIND_BBOX_FALLBACK
    except RenderError:
        raise
    except Exception:
        try:
            vis_segs = _render_bbox_fallback(shape, view_dir)
            hid_segs = []
            render_kind = RENDER_KIND_BBOX_FALLBACK
        except Exception as e_fallback:
            raise RenderError(f"HLR and bounding box fallback both failed for shape: {e_fallback}") from e_fallback

    # 2. Viewport bounds and scaling calculation
    all_points: List[Tuple[float, float]] = []
    for s in vis_segs:
        all_points.extend(s)
    for s in hid_segs:
        all_points.extend(s)

    if not all_points:
        raise RenderError("Cannot fit empty/degenerate projection to viewport: no points extracted")

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x < 1e-6 and span_y < 1e-6:
        raise RenderError("Cannot fit empty/degenerate projection to viewport: points collapse to zero area")

    pad_px = 30
    top_reserve = 45     # Banner reserve + padding
    bottom_reserve = 65  # Title & subtitle reserve

    avail_w = max(10, width - 2 * pad_px)
    avail_h = max(10, height - top_reserve - bottom_reserve - 2 * pad_px)

    scale_x = avail_w / (span_x if span_x > 1e-6 else 1.0)
    scale_y = avail_h / (span_y if span_y > 1e-6 else 1.0)
    scale = min(scale_x, scale_y)

    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    target_mid_x = width / 2.0
    target_mid_y = top_reserve + (height - top_reserve - bottom_reserve) / 2.0

    def to_pixel(x: float, y: float) -> Tuple[float, float]:
        px = target_mid_x + (x - mid_x) * scale
        py = target_mid_y - (y - mid_y) * scale  # Y-axis inverted for canvas
        return px, py

    # 3. Canvas and Banner
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    banner_h = 32
    if geometry_precision == "VALID_PER_PANEL":
        # Green status bar for physically verified B-Rep solids
        draw.rectangle([(0, 0), (width, banner_h)], fill="#2E7D32")
        draw.text((15, 9), BANNER_TEXT_VALID, fill="white", font=font)
    else:
        # Red warning banner for kinematic preview (fallback)
        draw.rectangle([(0, 0), (width, banner_h)], fill="#C62828")
        banner_msg = geometry_warning or BANNER_TEXT
        draw.text((15, 9), banner_msg, fill="white", font=font)

    # 4. Draw hidden dashed edges first
    for seg in hid_segs:
        for idx in range(len(seg) - 1):
            p1 = to_pixel(seg[idx][0], seg[idx][1])
            p2 = to_pixel(seg[idx + 1][0], seg[idx + 1][1])
            _draw_dashed_line(draw, p1, p2, fill="#BDBDBD", width=1, dash=5.0, gap=4.0)

    # 5. Draw visible solid edges
    for seg in vis_segs:
        px_pts = [to_pixel(pt[0], pt[1]) for pt in seg]
        if len(px_pts) >= 2:
            draw.line(px_pts, fill="black", width=2)

    # 6. Bottom metadata
    y_title = height - bottom_reserve + 10
    draw.text((15, y_title), title, fill="black", font=font)

    # Append hidden edges dropped info to subtitle if any
    display_subtitle = subtitle
    if hid_skipped > 0:
        suffix = f" | [{hid_skipped} hidden edges dropped]"
        display_subtitle = f"{subtitle}{suffix}" if subtitle else suffix.lstrip(" | ")

    if display_subtitle:
        draw.text((15, y_title + 18), display_subtitle, fill="#616161", font=font)

    # 7. Save output
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(out_file, format="PNG")
    except Exception as e:
        raise RenderError(f"Failed to save PNG image to {out_file}: {e}") from e

    return render_kind
