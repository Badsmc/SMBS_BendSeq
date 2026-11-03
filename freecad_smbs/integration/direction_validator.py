"""Compare cumulative rigid-panel preview with the actual folded FreeCAD source.

Heuristic rigid-surface registration: only returns VERIFIED when a unique,
well-separated reference fit exists. No machine or collision validation.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import sqrt
from smbs_core.kinematics2d.panel_kinematics import FoldState

@dataclass(frozen=True)
class DirectionResult:
    bend_id: str
    original_deg: float
    suggested_deg: float
    status: str

@dataclass(frozen=True)
class ValidationResult:
    status: str
    root_panel: str
    source_object: str
    rms_mm: float
    runner_up_mm: float
    directions: tuple
    message: str

def _dot(a,b):
    return sum(x*y for x,y in zip(a,b))

def _unit(v):
    n=sqrt(_dot(v,v))
    if n < 1e-9: raise ValueError("Zero reference direction")
    return tuple(x/n for x in v)

def _cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])

def _sub(a,b):
    return tuple(x-y for x,y in zip(a,b))

def _xyz(p):
    return (float(p.x),float(p.y),float(p.z))

def _reference_faces(shape):
    faces=[]
    for f in shape.Faces:
        if "Plane" not in type(f.Surface).__name__ or f.Area < 0.01:
            continue
        try:
            n=_unit(_xyz(f.normalAt(0,0)))
            c=_xyz(f.CenterOfMass)
            edges=[]
            for edge in f.Edges:
                if len(edge.Vertexes)==2 and edge.Length > 0.1:
                    d=_sub(_xyz(edge.Vertexes[1].Point),_xyz(edge.Vertexes[0].Point))
                    edges.append(_unit(d))
            if edges:
                faces.append((c,n,float(f.Area),edges))
        except (ValueError,RuntimeError):
            continue
    return faces

def _registrations(root_centroid, root_area, faces):
    """Map flat XY into the source face plane; preserve root face centroid."""
    x0,y0=root_centroid
    for center,normal,area,edges in faces:
        if abs(area-root_area)/max(root_area,1) > 0.40:
            continue
        for e in edges:
            for sign in (-1,1):
                u=tuple(sign*x for x in e)
                for nsign in (-1,1):
                    n=tuple(nsign*x for x in normal)
                    v=_cross(n,u)
                    def transform(p,u=u,v=v,n=n,center=center):
                        x,y,z=p
                        return tuple(center[i]+(x-x0)*u[i]+(y-y0)*v[i]+z*n[i] for i in range(3))
                    yield transform

def _score(graph,root,panels,reference,transform,overrides):
    state=FoldState(graph,root)
    for bend in graph.rooted(root):
        state.set_angle(bend.bend_id,overrides[bend.bend_id])
    residual=0.0
    for panel_id,(xy,area) in panels.items():
        p=state.transforms[panel_id].point((xy[0],xy[1],0))
        p=transform(p)
        normal=state.transforms[panel_id].point((0,0,1))
        origin=state.transforms[panel_id].point((0,0,0))
        n=_unit(_sub(transform(normal),transform(origin)))
        best=min((sum((p[i]-c[i])**2 for i in range(3))
                  + 25*(1-abs(_dot(n,rn)))**2
                  + 0.01*(sqrt(area)-sqrt(ra))**2)
                 for c,rn,ra,_ in reference)
        residual+=best
    return sqrt(residual/len(panels))

def validate_directions(graph,root,panel_build,source_shape,source_name):
    """Compare the final cumulative pose against planar faces in the source.

    Search signs of all bends for <=12 hinges, across candidate root
    registrations. Ambiguous or weak matches are UNVERIFIED, not corrections.
    """
    from itertools import product
    if len(graph.edges)>12:
        raise ValueError("Exhaustive sign verification limited to 12 bends")
    panels={p.panel_id:(p.centroid_xy,p.area_mm2) for p in panel_build.panels}
    if root not in panels:
        raise ValueError("Unknown root panel")
    reference=_reference_faces(source_shape)
    if not reference:
        raise ValueError("Source has no usable planar reference faces")
    rooted=graph.rooted(root)
    bend_ids=tuple(b.bend_id for b in rooted)
    targets={b.bend_id:b.angle_deg for b in rooted}
    candidates=list(_registrations(panels[root][0],panels[root][1],reference))
    if not candidates:
        raise ValueError("No source face matches fixed panel area within 40 percent")
    # Exhaustive search can be slow; reject pathological reference geometry.
    if len(candidates)>160:
        raise ValueError("Too many candidate registrations (%d)"%len(candidates))
    scores=[]
    for transform in candidates:
        for signs in product((1,-1),repeat=len(bend_ids)):
            angles={bend:targets[bend]*sign for bend,sign in zip(bend_ids,signs)}
            score=_score(graph,root,panels,reference,transform,angles)
            scores.append((score,signs))
    scores.sort(key=lambda x:x[0])
    best,signs=scores[0]
    # Compare with best solution having a DIFFERENT bend-sign assignment.
    alternate=next((s for s,other in scores if other!=signs),float("inf"))
    # Conservative: score < 2 mm and competing sign assignment >= 20% worse.
    reliable=best<2.0 and alternate>max(best*1.20,best+0.5)
    directions=tuple(DirectionResult(
        bid,targets[bid],targets[bid]*sign,
        ("VERIFIED" if sign==1 else "REVERSE_VERIFIED") if reliable else "UNVERIFIED"
    ) for bid,sign in zip(bend_ids,signs))
    return ValidationResult(
        "VERIFIED" if reliable else "UNVERIFIED",root,source_name,best,alternate,
        directions,
        "Rigid planar-face matching only. Radius, thickness and collisions not verified."
        if reliable else
        "No unique high-confidence fit. Do not auto-apply directions.")
