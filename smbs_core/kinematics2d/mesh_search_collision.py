"""Search-only sampled mesh penetration. No boolean or distance OCC calls.

A sampled point must be inside the other closed mesh and farther than the
penetration allowance from every triangle. Missing intersections between
sample points remain unverified; this is never an exact collision certificate.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from math import sqrt

_MODE = ContextVar('smbs_mesh_search', default=None)


def sub(a,b): return tuple(x-y for x,y in zip(a,b))
def dot(a,b): return sum(x*y for x,y in zip(a,b))
def cross(a,b): return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def box(points): return tuple(min(p[i] for p in points) for i in range(3))+tuple(max(p[i] for p in points) for i in range(3))
def gap2(p,b): return sum(max(b[i]-p[i],0,p[i]-b[i+3])**2 for i in range(3))


def triangle_distance2(p, t):
    a,b,c=t; ab=sub(b,a); ac=sub(c,a); n=cross(ab,ac); nn=dot(n,n)
    ap=sub(p,a)
    if nn>1e-24:
        h=dot(ap,n)/nn
        q=tuple(p[i]-h*n[i] for i in range(3))
        signs=[dot(cross(sub(v,u),sub(q,u)),n) for u,v in ((a,b),(b,c),(c,a))]
        if min(signs)>=-1e-12: return dot(ap,n)**2/nn
    best=float('inf')
    for u,v in ((a,b),(b,c),(c,a)):
        d=sub(v,u); den=dot(d,d)
        x=max(0,min(1,dot(sub(p,u),d)/den)) if den else 0
        best=min(best,sum((p[i]-u[i]-x*d[i])**2 for i in range(3)))
    return best


_RAY=(1.0,0.3713906763541037,0.529103187)

def ray_hit(p,t):
    a,b,c=t; e1=sub(b,a); e2=sub(c,a); h=cross(_RAY,e2); det=dot(e1,h)
    if abs(det)<1e-12:return None
    inv=1/det; s=sub(p,a); u=inv*dot(s,h)
    if u < -1e-10 or u>1+1e-10:return None
    q=cross(s,e1); v=inv*dot(_RAY,q)
    if v < -1e-10 or u+v>1+1e-10:return None
    distance=inv*dot(e2,q)
    return distance if distance>1e-9 else None


def ray_box(p,b):
    lo,hi=0,float('inf')
    for i in range(3):
        x,y=(b[i]-p[i])/_RAY[i],(b[i+3]-p[i])/_RAY[i]
        lo=max(lo,min(x,y));hi=min(hi,max(x,y))
    return hi>=lo


class Tree:
    def __init__(self, triangles):
        self.bounds=box([v for t in triangles for v in t]); self.children=();self.triangles=()
        if len(triangles)<=8:self.triangles=tuple(triangles)
        else:
            axis=max(range(3), key=lambda i:self.bounds[i+3]-self.bounds[i])
            ts=sorted(triangles,key=lambda t:sum(v[axis] for v in t));mid=len(ts)//2
            self.children=(Tree(ts[:mid]),Tree(ts[mid:]))

    def near(self,p,r2):
        if gap2(p,self.bounds)>r2:return False
        return (any(triangle_distance2(p,t)<=r2 for t in self.triangles)
                or any(c.near(p,r2) for c in self.children))

    def hits(self,p):
        if not ray_box(p,self.bounds):return []
        out=[x for t in self.triangles if (x:=ray_hit(p,t)) is not None]
        for c in self.children:out.extend(c.hits(p))
        return out

    def deep_inside(self,p,allowance):
        if gap2(p,self.bounds)>0 or self.near(p,allowance*allowance):return False
        hits=sorted(self.hits(p));unique=[]
        for x in hits:
            if not unique or abs(x-unique[-1])>1e-7:unique.append(x)
        return len(unique)%2==1


class MeshOracle:
    def __init__(self,tolerance_mm=1.0,deflection_mm=0.1):
        if tolerance_mm<=0 or deflection_mm<=0:raise ValueError('Invalid mesh tolerances')
        self.allowance=tolerance_mm+2*deflection_mm
        self.deflection=deflection_mm;self.cache={};self.queries=0;self.builds=0

    def mesh(self,shape):
        # FreeCAD hash includes the location. Orientation reversal leaves parity unchanged.
        key=(shape.hashCode(),str(shape.Placement))
        if key not in self.cache:
            vertices,indices=shape.tessellate(self.deflection)
            points=[(float(v.x),float(v.y),float(v.z)) for v in vertices]
            triangles=[tuple(points[i] for i in ids) for ids in indices]
            if not triangles:raise ValueError('Empty mesh')
            # Every edge must have two incident faces; open meshes cannot support parity.
            edges={}
            for ids in indices:
                for i,j in ((ids[0],ids[1]),(ids[1],ids[2]),(ids[2],ids[0])):
                    edge=tuple(sorted((points[i],points[j])));edges[edge]=edges.get(edge,0)+1
            if any(n!=2 for n in edges.values()):raise ValueError('Mesh is not closed/manifold')
            samples=set(points)
            for t in triangles:
                samples.add(tuple(sum(v[i] for v in t)/3 for i in range(3)))
                for a,b in ((t[0],t[1]),(t[1],t[2]),(t[2],t[0])):
                    samples.add(tuple((a[i]+b[i])/2 for i in range(3)))
            self.cache[key]=(Tree(triangles),tuple(samples));self.builds+=1
            if len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
        return self.cache[key]

    def collision(self,a,b):
        self.queries+=1
        ta,pa=self.mesh(a);tb,pb=self.mesh(b)
        for tree,points in ((tb,pa),(ta,pb)):
            if any(tree.deep_inside(p,self.allowance) for p in points):return True
        return False


@contextmanager
def mesh_search_mode(oracle):
    token=_MODE.set(oracle)
    try:yield
    finally:_MODE.reset(token)


def mesh_pair_result(a,b,label=''):
    oracle=_MODE.get()
    if oracle is None:return None
    from .native_collision_3d import PairClearanceResult, CollisionClass, compute_aabb_gap
    gap=compute_aabb_gap(a.BoundBox,b.BoundBox)
    try:
        collision=False if gap is not None and gap>0 else oracle.collision(a,b)
        status=CollisionClass.PENETRATION if collision else CollisionClass.NEAR_CONTACT
        reason='MESH_SAMPLED_DEPTH_GT_ALLOWANCE' if collision else 'MESH_NO_DEEP_SAMPLED_POINT_UNVERIFIED'
    except Exception as exc:
        status=CollisionClass.INVALID_GEOMETRY;reason='MESH_UNVERIFIED:'+str(exc)
    return PairClearanceResult(status,reason,0.0,0.0,gap,0.0,
                               is_lower_bound=True,tool_label=label)
