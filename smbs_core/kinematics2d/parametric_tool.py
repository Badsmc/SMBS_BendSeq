"""Original generic tooling profiles; demonstration geometry, not manufacturing designs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, cos, degrees, isfinite, radians, sin, tan
from pathlib import Path
import re

from smbs_core.kinematics2d.tooling_25d import (
    ArcSegment2D, Contour2D, LineSegment2D, has_self_intersection,
)


@dataclass(frozen=True)
class ToolRecipe:
    tool_id: str
    kind: str = "punch"
    height: float = 100.0
    width: float = 40.0
    angle: float = 88.0
    radius: float = 0.8
    opening: float = 12.0
    length: float = 100.0
    quantity: int = 0
    shank_width: float = 20.0
    shank_height: float = 15.0
    relief_depth: float = 0.0
    relief_height: float = 25.0
    base_width: float = 0.0
    base_height: float = 0.0
    body_offset: float = 0.0
    base_offset: float = 0.0
    base_right_height: float | None = None
    relief_start: float = 2.0
    relief_transition: float = 1.0
    relief_side: str = "right"
    punch_offset: float = 0.0

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", self.tool_id):
            raise ValueError("Use an ASCII tool ID containing letters, digits, underscores or hyphens.")
        if self.kind not in ("punch", "die"):
            raise ValueError("Unknown tool kind")
        for key in ("height", "width", "radius", "length"):
            value = getattr(self, key)
            if not isfinite(value) or value <= 0 or value > 5000:
                raise ValueError(f"Invalid positive dimension: {key}")
        if not isfinite(self.angle) or not 15 <= self.angle <= 120:
            raise ValueError("Included angle must be between 15 and 120 degrees")
        if type(self.quantity) is not int or not 0 <= self.quantity <= 10000:
            raise ValueError("Quantity must be an integer between 0 and 10000")
        if self.kind == "punch":
            if not (0 < self.shank_width <= self.width and 0 < self.shank_height < self.height):
                raise ValueError("Invalid mounting shank dimensions")
            if not (0 <= self.relief_depth < self.width - 4 and self.relief_height > 0):
                raise ValueError("Invalid relief dimensions")
            if not isfinite(self.punch_offset):
                raise ValueError("Punch body offset must be finite")
            if self.relief_side not in ("left", "right"):
                raise ValueError("Invalid relief side")
            if not (isfinite(self.relief_start) and self.relief_start >= 0
                    and isfinite(self.relief_transition) and self.relief_transition > 0
                    and 2*self.relief_transition < self.relief_height):
                raise ValueError("Invalid relief position or transition")
        else:
            if not isfinite(self.opening) or not 0 < self.opening < self.width:
                raise ValueError("V opening must be smaller than the body width")
            if not all(isfinite(v) and v >= 0 for v in (self.base_width, self.base_height)):
                raise ValueError("Invalid die base dimensions")
            if self.base_width or self.base_height:
                if not (self.width < self.base_width <= 5000 and 0 < self.base_height < self.height):
                    raise ValueError("Stepped die requires a wider base and a positive base height")
            right_height = self.base_height if self.base_right_height is None else self.base_right_height
            if not all(isfinite(v) for v in (self.body_offset, self.base_offset, right_height)):
                raise ValueError("Non-finite die dimensions")
            if abs(self.body_offset) >= self.width/2:
                raise ValueError("Die body must straddle the working axis")
            if self.base_width:
                if not (0 < right_height < self.height
                        and self.base_width/2-abs(self.base_offset) > 0):
                    raise ValueError("Invalid right base height or offset")
                if (self.base_width/2-self.base_offset <= self.width/2-self.body_offset
                        or self.base_width/2+self.base_offset <= self.width/2+self.body_offset):
                    raise ValueError("Both base ledges must extend beyond the upper body")
            elif self.base_offset or right_height:
                raise ValueError("Base dimensions require a stepped die")


def _lines(points):
    return [LineSegment2D(a, b) for a, b in zip(points, points[1:])]


def punch_layout(recipe: ToolRecipe):
    """Shared coordinates: tip and tangent flanks stay fixed on the working axis.

    Only the body translates by punch_offset. Both the working tip and
    mounting shank remain on X=0. Horizontal shoulders connect the centered
    shank to the shifted body at H1, with a positive overlap at that plane.
    """
    w, r = recipe.width/2, recipe.radius
    a = radians(recipe.angle/2)
    x, y = r*cos(a), r*(1-sin(a))
    left, right = recipe.punch_offset-w, recipe.punch_offset+w
    top = recipe.height-recipe.shank_height
    if left >= -x or right <= x:
        raise ValueError("Punch offset is too large for the fixed tip")
    ly, ry = y+(-left-x)/tan(a), y+(right-x)/tan(a)
    # Both tangent flank lines need a usable length for the native datum extractor.
    if min(-left-x, right-x)/sin(a) < 2.1:
        raise ValueError("Punch offset leaves an insufficient tip flank")
    if max(ly,ry)+4 >= top:
        raise ValueError("Punch body is too short or narrow for the tip and angle")
    sw = recipe.shank_width/2
    if min(right,sw)-max(left,-sw) <= 1e-5:
        raise ValueError("Centered mounting shank does not overlap the punch body")
    return x,y,left,right,ly,ry,top,-sw,sw


def build_contour(recipe: ToolRecipe) -> Contour2D:
    """Build one closed XY profile; Z is the longitudinal extrusion axis."""
    recipe.validate()
    h, w, r = recipe.height, recipe.width / 2, recipe.radius
    a = radians(recipe.angle / 2)
    if recipe.kind == "punch":
        x,y,lx,rx,ly,ry,top,sl,sr = punch_layout(recipe)
        def side(bottom, wall_x, shoulder, shank_x, sign):
            points = [bottom,(wall_x,shoulder)]
            if recipe.relief_depth and recipe.relief_side == ("right" if sign==1 else "left"):
                low = shoulder+recipe.relief_start
                high = low+recipe.relief_height
                if high+2 >= top:
                    raise ValueError("Relief does not fit between the tip and mounting shank")
                inner = wall_x-sign*recipe.relief_depth
                t = recipe.relief_transition
                points += [(wall_x,low),(inner,low+t),(inner,high-t),(wall_x,high)]
            points += [(wall_x,top),(shank_x,top),(shank_x,h)]
            return [p for i,p in enumerate(points) if i==0 or p!=points[i-1]]
        right = side((x,y),rx,ry,sr,1)
        left = side((-x,y),lx,ly,sl,-1)
        edges = [ArcSegment2D((-x,y),(x,y),(0,r),r,True)] + _lines(right+list(reversed(left)))
    else:
        v = recipe.opening / 2
        depth = v / tan(a)
        center_x = v + r * (1 - sin(a)) / cos(a)
        tx = center_x - r * cos(a)
        ty = -r + r * sin(a)
        left_x, right_x = recipe.body_offset-w, recipe.body_offset+w
        if center_x + 1 >= min(-left_x, right_x) or tx <= 0 or depth + 2 >= h or ty <= -depth:
            raise ValueError("V groove or shoulder radius does not fit the die body")
        if recipe.base_width:
            b = recipe.base_width / 2
            bl, br = recipe.base_offset-b, recipe.base_offset+b
            ll = -h + recipe.base_height
            rr = -h + (recipe.base_height if recipe.base_right_height is None else recipe.base_right_height)
            right_body = [(bl,-h),(br,-h),(br,rr),(right_x,rr),(right_x,0),(center_x,0)]
            left_body = [(-center_x,0),(left_x,0),(left_x,ll),(bl,ll),(bl,-h)]
        else:
            right_body = [(left_x,-h),(right_x,-h),(right_x,0),(center_x,0)]
            left_body = [(-center_x,0),(left_x,0),(left_x,-h)]
        edges = _lines(right_body)
        edges.append(ArcSegment2D((center_x, 0), (tx, ty), (center_x, -r), r, True))
        edges += _lines([(tx, ty), (0, -depth), (-tx, ty)])
        edges.append(ArcSegment2D((-tx, ty), (-center_x, 0), (-center_x, -r), r, True))
        edges += _lines(left_body)
    contour = Contour2D(segments=tuple(edges))
    if not contour.is_closed(1e-7) or has_self_intersection(contour) or contour.area <= 0:
        raise ValueError("Generated profile is not a valid closed contour")
    return contour


def dxf_text(contour: Contour2D) -> str:
    """Portable ASCII DXF with millimetre units and exact LINE/ARC entities."""
    pairs = [(0,"SECTION"),(2,"HEADER"),(9,"$ACADVER"),(1,"AC1015"),
             (9,"$INSUNITS"),(70,4),(0,"ENDSEC"),(0,"SECTION"),(2,"ENTITIES")]
    for edge in contour.segments:
        if isinstance(edge, ArcSegment2D):
            start, end = (edge.start, edge.end) if edge.ccw else (edge.end, edge.start)
            def angle(p):
                return degrees(atan2(p[1]-edge.center[1], p[0]-edge.center[0])) % 360
            pairs += [(0,"ARC"),(8,"0"),(10,edge.center[0]),(20,edge.center[1]),(30,0),
                      (40,edge.radius),(50,angle(start)),(51,angle(end))]
        else:
            pairs += [(0,"LINE"),(8,"0"),(10,edge.start[0]),(20,edge.start[1]),(30,0),
                      (11,edge.end[0]),(21,edge.end[1]),(31,0)]
    pairs += [(0,"ENDSEC"),(0,"EOF")]
    return "".join(f"{code}\n{value:.12g}\n" if isinstance(value,float)
                   else f"{code}\n{value}\n" for code,value in pairs)


def tooling_metadata(recipe: ToolRecipe) -> dict:
    y = recipe.height if recipe.kind == "punch" else -recipe.height
    punch = recipe.kind == "punch"
    datums = {
        "mounting_plane": {"kind":"plane","origin":[0,y,0],
                           "normal":[0,-1 if punch else 1,0],"tolerance_mm":0.05},
        "working_axis": {"kind":"axis","origin":[0,0,0],"direction":[0,0,1],
                         "tolerance_mm":0.02},
    }
    if punch:
        datums["tip_point"] = {"kind":"point","position":[0,0,0]}
    else:
        datums["v_center"] = {"kind":"axis","origin":[0,0,0],"direction":[0,0,1]}
    metadata = {"angle_deg":recipe.angle,"height_mm":recipe.height,
                "reference_length_mm":recipe.length,
                "source_dxf":recipe.tool_id+".dxf",
                "segments":[{"length_mm":recipe.length,"quantity":recipe.quantity}] if recipe.quantity else [],
                "provenance":"Original SMBS parametric demonstration geometry",
                "license":"CC-BY-SA-4.0", "manufacturing_certified":False,
                "parametric_recipe":asdict(recipe)}
    if punch:
        metadata.update(radius_mm=recipe.radius,working_height_mm=recipe.height-recipe.shank_height,
                        punch_offset_mm=recipe.punch_offset, shank_center_x_mm=0.0,
                        punch_offset_mode="body_only_fixed_tip_and_shank")
    else:
        metadata.update(v_width_mm=recipe.opening,shoulder_radius_mm=recipe.radius,
                        base_width_mm=recipe.base_width,base_height_mm=recipe.base_height)
    return {"schema_version":"1.0","id":recipe.tool_id,"kind":recipe.kind,
            "source":recipe.tool_id+".FCStd","datums":datums,"metadata":metadata}


def starter_recipes():
    """Original starter recipes; zero quantity until the operator defines stock."""
    yield ToolRecipe("SMBS_DEMO_P88", height=100, width=40)
    yield ToolRecipe("SMBS_DEMO_P85", height=100, width=40, angle=85, radius=1)
    yield ToolRecipe("SMBS_DEMO_P90", height=100, width=40, angle=90, radius=2)
    yield ToolRecipe("SMBS_DEMO_P30", height=140, width=36, angle=30, radius=0.5)
    yield ToolRecipe("SMBS_DEMO_GOOSENECK88", height=125, width=45, relief_depth=24, relief_height=35)
    for opening in (6,8,10,12,16,20,25,32,40,50,80):
        yield ToolRecipe(f"SMBS_DEMO_V{opening}", kind="die", opening=opening,
                         width=max(24,opening+16), height=max(35,opening), radius=min(2,opening/6))
    yield ToolRecipe("SMBS_DEMO_TV10", kind="die", opening=10, width=16,
                     height=80, radius=0.5, base_width=60, base_height=18)
    yield ToolRecipe("SMBS_DEMO_TV25", kind="die", opening=25, width=36,
                     height=80, radius=1, base_width=60, base_height=18)
