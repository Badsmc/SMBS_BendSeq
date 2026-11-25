"""Dimension geometry for the live editor, independent of Qt and FreeCAD."""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin, tan

from smbs_core.kinematics2d.parametric_tool import ToolRecipe, build_contour


@dataclass(frozen=True)
class Dimension:
    key: str
    code: str
    value: float
    axis: str
    start: tuple[float, float]
    end: tuple[float, float]
    lane: int = 1


def drawing_model(recipe: ToolRecipe):
    """Return the exact export contour plus dimension anchors in CAD XY.

    Positive lanes are right/bottom and negative lanes left/top in the preview.
    Angle and radius annotations use leaders rather than false scale dimensions.
    """
    contour = build_contour(recipe)
    h, w, r = recipe.height, recipe.width / 2, recipe.radius
    a = radians(recipe.angle / 2)
    dims = []
    def add(key, code, value, axis, p, q, lane=1):
        dims.append(Dimension(key, code, value, axis, p, q, lane))
    if recipe.kind == "punch":
        sw = recipe.shank_width / 2
        top = h - recipe.shank_height
        add("height", "H2", h, "v", (0,0), (sw,h), 2)
        add("working_height", "H1", top, "v", (0,0), (w,top), 1)
        add("width", "W", 2*w, "h", (-w,top), (w,top), -2)
        add("shank_width", "S", 2*sw, "h", (-sw,h), (sw,h), -1)
        add("shank_height", "H2-H1", recipe.shank_height, "v", (-sw,top), (-sw,h), -1)
        add("radius", "R", r, "leader", (0,0), (0,0), -1)
        add("angle", "α", recipe.angle, "leader", (r*cos(a),r*(1-sin(a))), (0,0), 1)
        if recipe.relief_depth:
            x, y = r*cos(a), r*(1-sin(a))
            shoulder = y + (w-x)/tan(a)
            low, high = shoulder+2, shoulder+2+recipe.relief_height
            add("relief_depth", "D", recipe.relief_depth, "h",
                (w-recipe.relief_depth, low+1), (w,low+1), 1)
            add("relief_height", "C", recipe.relief_height, "v", (w,low), (w,high), -2)
    else:
        b = recipe.base_width/2 if recipe.base_width else w
        v = recipe.opening/2
        depth = v/tan(a)
        center = v+r*(1-sin(a))/cos(a)
        add("height", "H", h, "v", (b,-h), (w,0), 2)
        add("width", "W", 2*w, "h", (-w,0), (w,0), -2)
        add("opening", "V", recipe.opening, "h", (-v,0), (v,0), -1)
        add("radius", "R", r, "leader", (center-r*cos(a),-r+r*sin(a)), (0,0), -1)
        add("angle", "α", recipe.angle, "leader", (0,-depth), (0,0), 1)
        if recipe.base_width:
            add("base_width", "B", recipe.base_width, "h", (-b,-h), (b,-h), 1)
            add("base_height", "T", recipe.base_height, "v", (-b,-h), (-b,-h+recipe.base_height), -1)
    return contour, tuple(dims)
