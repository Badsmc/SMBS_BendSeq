"""Dimensions anchored to the exact exported profile, independent of Qt."""
from __future__ import annotations
from dataclasses import dataclass
from math import cos, radians, sin, tan
from freecad.bendseq.core.kinematics2d.parametric_tool import ToolRecipe, build_contour, punch_layout


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
    contour = build_contour(recipe)
    h,w,r = recipe.height,recipe.width/2,recipe.radius
    a = radians(recipe.angle/2)
    dims = []
    def add(key,code,value,axis,p,q,lane=1):
        dims.append(Dimension(key,code,value,axis,p,q,lane))
    if recipe.kind == "punch":
        x,y,lx,rx,ly,ry,top,sl,sr = punch_layout(recipe)
        offset = recipe.punch_offset
        add("height","H2",h,"v",(0,0),(sr,h),2)
        add("working_height","H1",top,"v",(0,0),(rx,top),1)
        add("width","L6",2*w,"h",(lx,top),(rx,top),-2)
        add("shank_width","L5",recipe.shank_width,"h",(sl,h),(sr,h),-1)
        add("punch_offset","ΔX",offset,"h",(0,top),(offset,top),-3)
        add("shank_height","H2-H1",recipe.shank_height,"v",(sl,top),(sl,h),-1)
        add("radius","r",r,"leader",(0,0),(0,0),-1)
        add("angle","α",recipe.angle,"leader",(x,y),(0,0),1)
        sign = -1 if recipe.relief_side=="left" else 1
        wall, shoulder, other = (lx,ly,rx) if sign==-1 else (rx,ry,lx)
        add("tip_height","L1",shoulder,"v",(0,0),(wall,shoulder),-1)
        if recipe.relief_depth:
            low = shoulder+recipe.relief_start
            high = low+recipe.relief_height
            inner = wall-sign*recipe.relief_depth
            add("relief_start","L2",recipe.relief_start,"v",(wall,shoulder),(wall,low),-2)
            add("relief_height","L3",recipe.relief_height,"v",(wall,low),(wall,high),-2)
            add("relief_transition","L4",recipe.relief_transition,"v",(wall,low),(inner,low+recipe.relief_transition),-3)
            add("relief_depth","L7",recipe.relief_depth,"local_h",(inner,low+recipe.relief_transition),(wall,low+recipe.relief_transition),1)
            add("remaining_width","L8",2*w-recipe.relief_depth,"local_h",(other,(low+high)/2),(inner,(low+high)/2),-1)
    else:
        left,right = recipe.body_offset-w,recipe.body_offset+w
        b = recipe.base_width/2 if recipe.base_width else w
        bl,br = (recipe.base_offset-b,recipe.base_offset+b) if recipe.base_width else (left,right)
        v = recipe.opening/2
        depth = v/tan(a)
        center = v+r*(1-sin(a))/cos(a)
        add("height","H",h,"v",(br,-h),(right,0),2)
        add("width","W",2*w,"h",(left,0),(right,0),-3)
        add("left_width","L1",-left,"h",(left,0),(0,0),-2)
        add("right_width","L2",right,"h",(0,0),(right,0),-2)
        add("opening","V",recipe.opening,"h",(-v,0),(v,0),-1)
        add("radius","r",r,"leader",(center-r*cos(a),-r+r*sin(a)),(0,0),-1)
        add("angle","α",recipe.angle,"leader",(0,-depth),(0,0),1)
        add("groove_depth","L7",depth,"v",(0,-depth),(right,0),1)
        if recipe.base_width:
            lh=recipe.base_height
            rh=lh if recipe.base_right_height is None else recipe.base_right_height
            add("base_width","B",recipe.base_width,"h",(bl,-h),(br,-h),2)
            add("base_left","L3",-bl,"h",(bl,-h),(0,-h),1)
            add("base_right","L4",br,"h",(0,-h),(br,-h),1)
            add("base_height","L5",lh,"v",(bl,-h),(bl,-h+lh),-1)
            add("base_right_height","L6",rh,"v",(br,-h),(br,-h+rh),1)
            add("upper_left","L8",h-lh,"v",(left,-h+lh),(left,0),-1)
            add("upper_right","L9",h-rh,"v",(right,-h+rh),(right,0),1)
    return contour,tuple(dims)
