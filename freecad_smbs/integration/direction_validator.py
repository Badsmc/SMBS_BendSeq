"""2D v5.1: reference-face anchored, per-bend direction verification.

The SheetMetal unfold reference face fixes the root registration. Every
candidate is evaluated against distinct source planar faces using panel
centroids, areas, and normals. Ambiguous candidates are never auto-applied.
Rigid panels are an approximation: no radius/thickness/collision validation.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
from math import sqrt, isfinite
from smbs_core.kinematics2d.panel_kinematics import FoldState

@dataclass(frozen=True)
class DirectionResult:
    bend_id: str
    original_deg: float
    suggested_deg: float
    status: str
    margin_mm: float = 0.0

@dataclass(frozen=True)
class ValidationResult:
    status: str
    root_panel: str
    source_object: str
    rms_mm: float
    runner_up_mm: float
    directions: tuple
    message: str

def _dot(a,b): return sum(x*y for x,y in zip(a,b))
def _sub(a,b): return tuple(x-y for x,y in zip(a,b))
def _add(a,b): return tuple(x+y for x,y in zip(a,b))
def _mul(a,k): return tuple(x*k for x in a)
def _norm(v): return sqrt(_dot(v,v))
def _unit(v):
    n=_norm(v)
    if n<1e-9: raise ValueError("Degenerate direction")
    return _mul(v,1.0/n)
def _cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def _xyz(p): return (float(p.x),float(p.y),float(p.z))

def _faces(shape):
    result=[]
    for i,f in enumerate(shape.Faces,1):
        if "Plane" not in type(f.Surface).__name__ or f.Area<0.01:
            continue
        try:
            normal=_unit(_xyz(f.normalAt(0,0)))
            center=_xyz(f.CenterOfMass)
            edges=[]
            for edge in f.Edges:
                if len(edge.Vertexes)==2 and edge.Length>0.1:
                    v=_sub(_xyz(edge.Vertexes[1].Point),_xyz(edge.Vertexes[0].Point))
                    edges.append(_unit(v))
            result.append((i,center,normal,float(f.Area),tuple(edges)))
        except (RuntimeError,ValueError):
            continue
    return tuple(result)

def _registrations(root_xy,root_area,reference):
    """Anchor to a *specific* original face, with its directed normal.

    No opposite normal or arbitrary nearest-face root selection. Source face
    normal is used as a registration convention, not proof of sheet side.
    """
    _,center,n,area,edges=reference
    if abs(area-root_area)/max(area,root_area)>0.40:
        raise ValueError("Fixed panel does not match reference face area. "
                         "Select the actual unfold reference panel as Fixed panel.")
    x0,y0=root_xy
    seen=set()
    for e in edges:
        tangent=_sub(e,_mul(n,_dot(e,n)))
        if _norm(tangent)<1e-6: continue
        tangent=_unit(tangent)
        for s in (1,-1):
            u=_mul(tangent,s)
            v=_unit(_cross(n,u))
            key=tuple(round(x,5) for x in u+v)
            if key in seen: continue
            seen.add(key)
            def transform(p,u=u,v=v,n=n,center=center):
                x,y,z=p
                return _add(center,_add(_mul(u,x-x0),_add(_mul(v,y-y0),_mul(n,z))))
            yield transform

def _best_assignment(costs, root_index, reference_index):
    """Minimum-cost injective panel/face assignment (Hungarian algorithm).

    The fixed panel MUST match the actual SheetMetal reference face.
    """
    n=len(costs)
    m=len(costs[0])
    if m<n: return float("inf")
    matrix=[list(row) for row in costs]
    for i in range(n):
        for j in range(m):
            if (i==root_index)!=(j==reference_index):
                matrix[i][j]=1e12
    u=[0.0]*(n+1)
    v=[0.0]*(m+1)
    assignment=[0]*(m+1)
    way=[0]*(m+1)
    for i in range(1,n+1):
        assignment[0]=i
        j0=0
        minv=[float("inf")]*(m+1)
        used=[False]*(m+1)
        while True:
            used[j0]=True
            i0=assignment[j0]
            delta=float("inf")
            j1=0
            for j in range(1,m+1):
                if not used[j]:
                    cur=matrix[i0-1][j-1]-u[i0]-v[j]
                    if cur<minv[j]:
                        minv[j]=cur
                        way[j]=j0
                    if minv[j]<delta:
                        delta=minv[j]
                        j1=j
            for j in range(m+1):
                if used[j]:
                    u[assignment[j]]+=delta
                    v[j]-=delta
                else:
                    minv[j]-=delta
            j0=j1
            if assignment[j0]==0: break
        while True:
            j1=way[j0]
            assignment[j0]=assignment[j1]
            j0=j1
            if j0==0: break
    total=-v[0]
    return total if total<1e11 else float("inf")

def _score(graph,root,panels,faces,transform,angles,reference_index):
    state=FoldState(graph,root)
    for bend in graph.rooted(root):
        state.set_angle(bend.bend_id,angles[bend.bend_id])
    rows=[]
    for panel_id,(xy,area) in panels.items():
        pose=state.transforms[panel_id]
        p=transform(pose.point((xy[0],xy[1],0)))
        normal_point=transform(pose.point((xy[0],xy[1],1)))
        n=_unit(_sub(normal_point,p))
        row=[]
        for _,c,fn,fa,_ in faces:
            distance=_norm(_sub(p,c))
            orientation=1-abs(_dot(n,fn))
            area_error=abs(sqrt(area)-sqrt(fa))
            row.append(distance**2+100*orientation**2+0.1*area_error**2)
        rows.append(row)
    value=_best_assignment(rows,list(panels).index(root),reference_index)
    return sqrt(value/len(panels)) if isfinite(value) else float("inf")

def validate_directions(graph,root,panel_build,source_shape,source_name,
                        source_face=None):
    """Fail closed unless individual bend signs are uniquely supported.

    source_face is the original SheetMetal reference face, e.g. 'Face4'.
    No generic registration fallback is permitted.
    """
    if source_face is None or not str(source_face).startswith("Face"):
        raise ValueError("SheetMetal reference face is required for v5.1")
    try: face_index=int(str(source_face)[4:])
    except ValueError as exc: raise ValueError("Invalid source face") from exc
    if len(graph.edges)>12: raise ValueError("Maximum 12 bends for exhaustive validation")
    panels={p.panel_id:(p.centroid_xy,p.area_mm2) for p in panel_build.panels}
    if root not in panels: raise ValueError("Unknown fixed panel")
    faces=_faces(source_shape)
    if len(faces)<len(panels):
        raise ValueError("Not enough source planar faces for unique panel matching")
    ref=next((f for f in faces if f[0]==face_index),None)
    if ref is None: raise ValueError("Reference face is not planar")
    regs=tuple(_registrations(panels[root][0],panels[root][1],ref))
    if not regs: raise ValueError("No reference edge directions")
    rooted=graph.rooted(root)
    bids=tuple(b.bend_id for b in rooted)
    targets={b.bend_id:b.angle_deg for b in rooted}
    results=[]
    for reg in regs:
        for signs in product((1,-1),repeat=len(bids)):
            angles={bid:targets[bid]*s for bid,s in zip(bids,signs)}
            score=_score(graph,root,panels,faces,reg,angles,\n                         next(i for i,f in enumerate(faces) if f[0]==face_index))
            if isfinite(score): results.append((score,signs))
    if not results:
        raise ValueError("No valid one-to-one panel-to-face matching")
    results.sort(key=lambda t:t[0])
    best,signs=results[0]
    directions=[]
    for idx,bid in enumerate(bids):
        opposite=min((score for score,other in results
                      if other[idx]!=signs[idx]),default=float("inf"))
        margin=opposite-best
        # 2mm tolerance and >=0.75mm separation for EACH bend.
        verified=best<2.0 and margin>max(0.75,0.2*best)
        directions.append(DirectionResult(
            bid,targets[bid],targets[bid]*signs[idx],
            ("VERIFIED" if signs[idx]==1 else "REVERSE_VERIFIED")
            if verified else "UNVERIFIED",margin))
    all_verified=all(d.status!="UNVERIFIED" for d in directions)
    runner=min((s for s,other in results if other!=signs),default=float("inf"))
    return ValidationResult(
        "VERIFIED" if all_verified else "UNVERIFIED",
        root,source_name,best,runner,tuple(directions),
        ("All bend signs independently separated; rigid-face approximation only."
         if all_verified else
         "Some signs are ambiguous. No auto-apply. Check source face, "
         "fixed panel, symmetry and panel-to-face registration.")
    )
