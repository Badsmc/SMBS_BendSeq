"""
panel_face_resolver.py - Runtime Panel to BRep Face Matching for SMBS (KL-12)

SPEC / Architecture Requirement:
Matches topological B-Rep faces of a shape to canonical panels using plane fitting
(centroid and normal) and assigns cylindrical bend faces to the child panel of the
associated bend (v1-approximation for per-panel kinematics).
Does NOT modify canonical_part.schema.json.
Preserves existing non-empty face_indices.
Safe against non-BRep objects (e.g. MagicMock in unit tests).

Known limitation (KL-12 v2):
- Side edges (planar faces perpendicular to a panel normal) are assigned to
  the nearest panel by perpendicular-plane distance.
- Bridge caps spanning multiple panels across a bend line (fixture_l faces
  1, 4) cannot be rigidly assigned to a single panel — they stay unassigned.
  Impact: such caps remain stationary during rotate_subtree (KL-22 trade-off
  preserved). Deferred to KL-12 v3 (planar self-collision).
"""

import math
import logging
from typing import Dict, Any, List, Tuple, Optional

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_VERTEX
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

from smbs_core.Core.panel_graph import PanelGraph

logger = logging.getLogger(__name__)


def _dot(v1: Tuple[float, float, float], v2: Tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))


def _norm(v: Tuple[float, float, float]) -> float:
    return math.sqrt(sum(a * a for a in v))


def _is_parallel(v1: Tuple[float, float, float], v2: Tuple[float, float, float], tol: float = 1e-2) -> bool:
    n1 = _norm(v1)
    n2 = _norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return False
    d = abs(_dot(v1, v2)) / (n1 * n2)
    return abs(d - 1.0) < tol


def _dist_point_to_plane(pt: Tuple[float, float, float], plane_pt: Tuple[float, float, float], plane_norm: Tuple[float, float, float]) -> float:
    vec = tuple(a - b for a, b in zip(pt, plane_pt))
    pn_norm = _norm(plane_norm)
    if pn_norm < 1e-6:
        return float('inf')
    return abs(_dot(vec, plane_norm)) / pn_norm


def _dist_sq(p1: Tuple[float, float, float], p2: Tuple[float, float, float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(p1, p2))


def resolve_panel_faces(
    shape: Any,
    panel_graph: PanelGraph,
    canonical_payload: Dict[str, Any]
) -> None:
    """
    Populate face_indices for panels in panel_graph using geometric plane matching
    against shape faces.
    
    Invariant rules:
    1. If shape is not TopoDS_Shape or is null, skips resolution safely without hanging.
    2. If panel.face_indices is already non-empty, it is preserved.
    3. Planar faces are assigned to the panel whose normal is parallel and whose
       plane (passing through canonical centroid) is within thickness/2 + 0.2 distance.
    4. Cylindrical faces are assigned to the child_panel_id of the nearest bend line
       (v1-approximation for subtree rotation).
    5. If thickness_mm is missing or invalid, raises ValueError.
    6. If any panel in panel_graph receives no planar faces after matching, raises
       ValueError with actionable diagnostics (invariant #3).
    """
    if shape is None or not hasattr(panel_graph, 'panels') or not panel_graph.panels:
        return

    # P1 fix: Strict type guard before any OCC calls to avoid hang on mock objects
    if not isinstance(shape, TopoDS_Shape):
        logger.debug(f"resolve_panel_faces: shape is not TopoDS_Shape ({type(shape).__name__}), skipping")
        return

    if shape.IsNull():
        logger.debug("resolve_panel_faces: shape is null, skipping")
        return

    # Invariant: preserve existing manual or pre-assigned face_indices
    panels_to_resolve = [p for p in panel_graph.panels.values() if not p.face_indices]
    if not panels_to_resolve:
        logger.debug("All panels already have face_indices populated. Skipping resolver.")
        return

    # P3 fix: require thickness_mm without silent fallback
    thickness_info = canonical_payload.get("thickness_mm")
    if not isinstance(thickness_info, dict) or "value" not in thickness_info or thickness_info["value"] is None:
        raise ValueError("resolve_panel_faces: canonical_payload missing thickness_mm.value")
    thickness = float(thickness_info["value"])

    # P2 fix: threshold covers both faces of sheet + tolerance.
    # thickness/2 was insufficient for float-boundary (98.3 - 99.25 = -0.95 case).
    plane_dist_threshold = thickness + 0.2

    panels_meta = canonical_payload.get("panels", {})
    bends_meta = canonical_payload.get("bends", {})

    # 1. Extract all faces from shape
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    faces = []
    face_data = []
    while exp.More():
        f = exp.Current()
        idx = len(faces)
        faces.append(f)
        surf = BRepAdaptor_Surface(f)
        stype = surf.GetType()

        props = GProp_GProps()
        brepgprop.SurfaceProperties(f, props)
        area = props.Mass()
        cog = props.CentreOfMass()
        cog_tuple = (float(cog.X()), float(cog.Y()), float(cog.Z()))

        data = {
            "idx": idx,
            "face": f,
            "type": stype,
            "area": area,
            "cog": cog_tuple,
            "normal": None,
            "cyl_loc": None,
            "cyl_radius": None
        }

        if stype == GeomAbs_Plane:
            pln = surf.Plane()
            ax = pln.Axis().Direction()
            data["normal"] = (float(ax.X()), float(ax.Y()), float(ax.Z()))
        elif stype == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            loc = cyl.Location()
            data["cyl_loc"] = (float(loc.X()), float(loc.Y()), float(loc.Z()))
            data["cyl_radius"] = float(cyl.Radius())

        face_data.append(data)
        exp.Next()

    # 2. Match planar faces to panels
    assigned_faces: Dict[str, List[int]] = {p.id: [] for p in panels_to_resolve}
    assigned_face_indices = set()

    for fd in face_data:
        if fd["type"] == GeomAbs_Plane and fd["normal"] is not None:
            best_pid: Optional[str] = None
            min_dist = float('inf')

            for panel in panels_to_resolve:
                p_info = panels_meta.get(panel.id, {})
                p_norm = tuple(p_info.get("normal", panel.normal or (0.0, 0.0, 1.0)))
                p_cent = tuple(p_info.get("centroid", (0.0, 0.0, 0.0)))

                if _is_parallel(fd["normal"], p_norm):
                    d = _dist_point_to_plane(fd["cog"], p_cent, p_norm)
                    if d <= plane_dist_threshold and d < min_dist:
                        min_dist = d
                        best_pid = panel.id

            if best_pid is not None:
                assigned_faces[best_pid].append(fd["idx"])
                assigned_face_indices.add(fd["idx"])

    # 2b. Match side edges (unassigned planar faces perpendicular to panel normal)
    for fd in face_data:
        if fd["idx"] in assigned_face_indices:
            continue
        if fd["type"] == GeomAbs_Plane and fd["normal"] is not None:
            best_pid: Optional[str] = None
            min_dist = float('inf')
            min_dist_cog = float('inf')

            for panel in panels_to_resolve:
                p_info = panels_meta.get(panel.id, {})
                p_norm = tuple(p_info.get("normal", panel.normal or (0.0, 0.0, 1.0)))
                p_cent = tuple(p_info.get("centroid", (0.0, 0.0, 0.0)))

                if not _is_parallel(fd["normal"], p_norm):
                    d = _dist_point_to_plane(fd["cog"], p_cent, p_norm)
                    if d <= plane_dist_threshold:
                        d_cog = _dist_sq(fd["cog"], p_cent)
                        if d < min_dist or (abs(d - min_dist) < 1e-4 and d_cog < min_dist_cog):
                            min_dist = d
                            min_dist_cog = d_cog
                            best_pid = panel.id

            if best_pid is not None:
                assigned_faces[best_pid].append(fd["idx"])
                assigned_face_indices.add(fd["idx"])

    # 3. Match cylindrical faces to bends -> child panels (v1-approximation)
    for fd in face_data:
        if fd["type"] == GeomAbs_Cylinder and fd["cyl_loc"] is not None:
            best_child_pid: Optional[str] = None
            min_bend_d = float('inf')

            for bid, bdata in bends_meta.items():
                ap = tuple(bdata.get("axis_point", (0.0, 0.0, 0.0)))
                child_pid = bdata.get("child_panel_id")
                d_sq = _dist_sq(fd["cyl_loc"], ap)
                if d_sq < min_bend_d:
                    min_bend_d = d_sq
                    best_child_pid = child_pid

            if best_child_pid is not None and best_child_pid in assigned_faces:
                assigned_faces[best_child_pid].append(fd["idx"])
                assigned_face_indices.add(fd["idx"])

    # 4. Check for unassigned panels and populate PanelGraph (Actionable fail on empty)
    for panel in panels_to_resolve:
        indices = assigned_faces.get(panel.id, [])
        if not indices:
            p_info = panels_meta.get(panel.id, {})
            raise ValueError(
                f"resolve_panel_faces failed: panel {panel.id} matched 0 faces "
                f"(centroid={p_info.get('centroid')}, normal={p_info.get('normal')}, "
                f"evaluated {len(face_data)} BRep faces in shape)"
            )
        panel.face_indices = sorted(indices)
        logger.debug(f"Resolved panel {panel.id} -> face_indices {panel.face_indices}")

    # 5. Classify remaining unassigned faces (bridge-caps vs relief cuts) with actionable diagnostics
    all_indices = set(fd["idx"] for fd in face_data)
    unassigned_indices = sorted(all_indices - assigned_face_indices)

    bridge_caps: List[int] = []
    face_diagnostics: Dict[int, Dict[str, Any]] = {}

    for u_idx in unassigned_indices:
        fd = face_data[u_idx]
        face_shape = fd.get("face")

        spanning_bends: List[str] = []
        adjacent_panels: set = set()

        if fd["type"] == GeomAbs_Plane and face_shape is not None and bends_meta:
            v_exp = TopExp_Explorer(face_shape, TopAbs_VERTEX)
            verts = []
            while v_exp.More():
                pnt = BRep_Tool.Pnt(v_exp.Current())
                verts.append((float(pnt.X()), float(pnt.Y()), float(pnt.Z())))
                v_exp.Next()

            if verts:
                for bid, bdata in bends_meta.items():
                    ap = tuple(bdata.get("axis_point", (0.0, 0.0, 0.0)))

                    p1_id = bdata.get("parent_panel_id") or bdata.get("panel_a_id")
                    p2_id = bdata.get("child_panel_id") or bdata.get("panel_b_id")

                    if (not p1_id or not p2_id) and hasattr(panel_graph, "_bend_edges") and bid in panel_graph._bend_edges:
                        p1_edge, p2_edge = panel_graph._bend_edges[bid]
                        p1_id = p1_id or p1_edge
                        p2_id = p2_id or p2_edge

                    if not p1_id and p2_id:
                        other_panels = [p for p in panels_meta.keys() if p != p2_id]
                        if len(other_panels) == 1:
                            p1_id = other_panels[0]

                    if not p1_id or not p2_id or p1_id not in panels_meta or p2_id not in panels_meta:
                        continue

                    c1 = tuple(panels_meta[p1_id].get("centroid", (0.0, 0.0, 0.0)))
                    c2 = tuple(panels_meta[p2_id].get("centroid", (0.0, 0.0, 0.0)))

                    ax_dir = bdata.get("axis_direction")
                    if ax_dir is None:
                        n1_vec = tuple(panels_meta[p1_id].get("normal", (0.0, 0.0, 1.0)))
                        n2_vec = tuple(panels_meta[p2_id].get("normal", (1.0, 0.0, 0.0)))
                        cross_d = (
                            n1_vec[1] * n2_vec[2] - n1_vec[2] * n2_vec[1],
                            n1_vec[2] * n2_vec[0] - n1_vec[0] * n2_vec[2],
                            n1_vec[0] * n2_vec[1] - n1_vec[1] * n2_vec[0],
                        )
                        c_norm = _norm(cross_d)
                        if c_norm > 1e-4:
                            ax_dir = tuple(d / c_norm for d in cross_d)
                        else:
                            ax_dir = (0.0, 1.0, 0.0)
                    else:
                        dir_norm = _norm(ax_dir)
                        if dir_norm < 1e-6:
                            ax_dir = (0.0, 1.0, 0.0)
                        else:
                            ax_dir = tuple(d / dir_norm for d in ax_dir)

                    def _perp_to_axis(v: Tuple[float, float, float], d_ax: Tuple[float, float, float]) -> Tuple[float, float, float]:
                        dot_p = sum(a * b for a, b in zip(v, d_ax))
                        return tuple(a - dot_p * b for a, b in zip(v, d_ax))

                    v1 = tuple(c - a for c, a in zip(c1, ap))
                    v2 = tuple(c - a for c, a in zip(c2, ap))
                    v1_perp = _perp_to_axis(v1, ax_dir)
                    v2_perp = _perp_to_axis(v2, ax_dir)

                    n1 = _norm(v1_perp)
                    n2 = _norm(v2_perp)
                    if n1 < 1e-3 or n2 < 1e-3:
                        continue
                    v1_u = tuple(a / n1 for a in v1_perp)
                    v2_u = tuple(a / n2 for a in v2_perp)

                    proj1 = [sum((vx - a) * d for vx, a, d in zip(pt, ap, v1_u)) for pt in verts]
                    proj2 = [sum((vx - a) * d for vx, a, d in zip(pt, ap, v2_u)) for pt in verts]

                    if max(proj1) > 0.5 and max(proj2) > 0.5:
                        spanning_bends.append(bid)
                        adjacent_panels.add(p1_id)
                        adjacent_panels.add(p2_id)

        if spanning_bends:
            bridge_caps.append(u_idx)
            face_diagnostics[u_idx] = {
                "face_index": u_idx,
                "classification": "BRIDGE_CAP",
                "spanning_bends": spanning_bends,
                "adjacent_panels": sorted(list(adjacent_panels)),
                "reason": (
                    f"Planar face {u_idx} spans across bend(s) {spanning_bends} "
                    f"connecting panels {sorted(list(adjacent_panels))}; "
                    f"cannot be rigidly assigned to a single moving panel"
                ),
                "area": fd["area"],
                "cog": fd["cog"],
            }
        else:
            face_diagnostics[u_idx] = {
                "face_index": u_idx,
                "classification": "UNASSIGNED_RELIEF",
                "reason": f"Face {u_idx} does not match canonical panel planes within threshold",
                "area": fd["area"],
                "cog": fd["cog"],
            }

    panel_graph.unassigned_faces = unassigned_indices
    panel_graph.face_diagnostics = face_diagnostics
    panel_graph.bridge_caps = bridge_caps

    if unassigned_indices:
        logger.info(
            f"resolve_panel_faces: {len(unassigned_indices)} faces remain unassigned "
            f"({len(bridge_caps)} bridge-caps: {bridge_caps}): {unassigned_indices}"
        )
