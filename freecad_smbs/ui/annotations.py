"""
annotations.py - FreeCAD 3D annotations rendering (Req 1, C4).
Copied from SMBS_AnnotateBends.FCMacro (option ii).

Headless-safe: FreeCAD / Part imports are strictly lazy inside rendering functions.
Pure-Python helper compute_annotation_positions is testable without FreeCAD GUI.
"""
from __future__ import annotations

from typing import Any, List, NamedTuple, Tuple

# Formatting constants copied from macro
ANNOTATION_Z_OFFSET_MM: float = 3.0
ANNOTATION_FONT_SIZE: int = 15


def compute_annotation_positions(
    annotations: List[Any],
    flat_zmax: float,
) -> List[Tuple[int, float, float, float, str]]:
    """
    Pure Python: compute (step, x, y, z, verification) per annotation.
    Z-coordinate overrides midpoint[2] with flat_zmax + ANNOTATION_Z_OFFSET_MM (macro parity).
    """
    z = flat_zmax + ANNOTATION_Z_OFFSET_MM
    return [
        (a.step, a.midpoint[0], a.midpoint[1], z, a.verification)
        for a in annotations
    ]


def render_flat_pattern_annotations(
    doc: Any,
    artifacts: Any,
    annotations: List[Any],
) -> List[Any]:
    """
    Render flat pattern BREP and 3D AnnotationLabels in active FreeCAD document.
    FreeCAD GUI context only.
    """
    import FreeCAD as App
    import Part
    from freecad_smbs.ui.badges import get_badge_rgba

    if artifacts.flat_pattern_path is None or not artifacts.flat_pattern_path.exists():
        raise RuntimeError("flat_pattern.brep not found in run artifacts")

    flat_shape = Part.Shape()
    flat_shape.read(str(artifacts.flat_pattern_path))
    flat_obj = Part.show(flat_shape, "SMBS_FlatPattern")

    try:
        flat_bb = flat_obj.Shape.BoundBox
        flat_zmax = flat_bb.ZMax
    except Exception:
        flat_zmax = 0.0

    created: List[Tuple[Any, str]] = []
    positions = compute_annotation_positions(annotations, flat_zmax)

    for step, x, y, z, verification in positions:
        label = doc.addObject("App::AnnotationLabel", f"SMBS_step_{step:03d}")
        label.LabelText = [str(step)]
        label.BasePosition = App.Vector(x, y, z)
        created.append((label, verification))

    try:
        doc.recompute()
    except Exception:
        pass

    # ViewObject styling (FreeCAD 1.1 App::AnnotationLabel contract from macro)
    for label, verification in created:
        vo = getattr(label, "ViewObject", None)
        if vo:
            rgba = get_badge_rgba(verification)
            try:
                vo.DisplayMode = "Object"
            except Exception:
                pass
            for attempt in (rgba, rgba[:3]):
                try:
                    vo.TextColor = attempt
                    break
                except Exception:
                    pass
            try:
                vo.FontSize = ANNOTATION_FONT_SIZE
            except Exception:
                pass
            try:
                vo.Frame = False
            except Exception:
                pass
            try:
                vo.BackgroundColor = (0.0, 0.0, 0.0, 0.0)
            except Exception:
                pass
            try:
                vo.Justification = "Center"
            except Exception:
                pass
            vo.Visibility = True

    try:
        doc.recompute()
    except Exception:
        pass

    return [lbl for lbl, _ in created]
