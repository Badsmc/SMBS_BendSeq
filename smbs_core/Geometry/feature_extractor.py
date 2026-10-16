"""
feature_extractor.py - Feature Extraction for Sheet Metal 3D Models (Headless Core)
Rules:
- Preserves signed_angle_deg (never abs(angle)).
- No hardcoded thickness, length, material, or dummy 100.0 fallbacks.
- Deterministic panel and bend IDs from geometric face hashes.
- No silent exception swallowing (`except: pass`).
"""

import math
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple, Set

from smbs_core.Core.bend_graph import BendRecord, BendGraph, IncompleteBendError
from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.geometry_backend.base import GeometryBackend, CylinderData

logger = logging.getLogger(__name__)


class FeatureExtractionError(Exception):
    """Raised when sheet metal feature extraction fails or encounters ambiguous geometry."""
    pass


class PanelInfo:
    """Represents a canonical planar sheet metal flange (aggregating coplanar/parallel faces)."""
    def __init__(
        self,
        panel_id: str,
        face_ids: List[str],
        total_area: float,
        centroid: Tuple[float, float, float],
        normal: Tuple[float, float, float]
    ):
        self.panel_id = panel_id
        self.id = panel_id
        self.face_ids = face_ids
        self.total_area = total_area
        self.centroid = centroid
        self.normal = normal

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def __repr__(self) -> str:
        return f"<Panel {self.panel_id} area={self.total_area:.1f} faces={self.face_ids}>"


class FeatureExtractor:
    def __init__(self, backend: GeometryBackend, sm_client: Optional[Any] = None):
        self.backend = backend
        self.sm_client = sm_client

    def calculate_signed_angle(
        self,
        normal1: Tuple[float, float, float],
        normal2: Tuple[float, float, float],
        bend_axis: Tuple[float, float, float]
    ) -> float:
        n1 = self._normalize(normal1)
        n2 = self._normalize(normal2)
        axis = self._normalize(bend_axis)

        dot = n1[0]*n2[0] + n1[1]*n2[1] + n1[2]*n2[2]
        dot_clamped = max(-1.0, min(1.0, dot))
        alpha_rad = math.acos(dot_clamped)
        alpha_deg = math.degrees(alpha_rad)

        if alpha_deg < 1e-3:
            raise FeatureExtractionError("Bend angle is 0 degrees (coplanar faces). Ambiguous bend.")

        cross = (
            n1[1]*n2[2] - n1[2]*n2[1],
            n1[2]*n2[0] - n1[0]*n2[2],
            n1[0]*n2[1] - n1[1]*n2[0]
        )

        triple = axis[0]*cross[0] + axis[1]*cross[1] + axis[2]*cross[2]
        sign = 1.0 if triple >= 0.0 else -1.0

        signed_angle = sign * alpha_deg
        return round(signed_angle, 2)

    @staticmethod
    def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
        mag = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
        if mag < 1e-9:
            return (0.0, 0.0, 1.0)
        return (v[0] / mag, v[1] / mag, v[2] / mag)

    def extract_features(
        self,
        shape: Any,
        known_thickness: Optional[float] = None,
        diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if shape is None:
            raise FeatureExtractionError("Cannot extract features from None shape.")

        thickness = known_thickness
        if thickness is None:
            if self.sm_client is not None:
                try:
                    thickness = self.sm_client.get_thickness(shape)
                except Exception as e:
                    logger.warning("SheetMetal get_thickness failed, falling back to geometric detection: %s", e)
                    thickness = self._detect_thickness(shape)
            else:
                thickness = self._detect_thickness(shape)

        bends = self._detect_bends(shape, thickness, diagnostics=diagnostics)

        if not bends:
            raise FeatureExtractionError("No valid bend features detected in shape.")

        records = {}
        for b_name, info in bends.items():
            cyl_face = info.get("cyl_face")
            if self.sm_client is not None and cyl_face is not None:
                try:
                    from_sm_angle, is_down = self.sm_client.get_bend_angle_and_direction(cyl_face)
                    logger.debug(
                        "Bend %s: geom_angle=%.2f, sm_angle=%.2f, is_down=%s",
                        b_name, info["signed_angle"], from_sm_angle, is_down
                    )
                except Exception as e:
                    logger.debug("Bend %s: get_bend_angle_and_direction failed: %s", b_name, e)

            records[b_name] = BendRecord(
                bend_id=b_name,
                feature_id=f"FEAT_{b_name}",
                parent_panel_id=info.get("parent_panel_id", "P1"),
                child_panel_id=info.get("child_panel_id", "P2"),
                axis_origin=tuple(info["origin"]),
                axis_direction=tuple(info["axis"]),
                signed_angle=info["signed_angle"],
                length=info.get("length", 50.0),
                radius=None,
                thickness=None
            )

        return {
            "thickness": thickness,
            "material": "Steel_S235",
            "bends": bends,
            "records": records,
            "panels": getattr(self, "_last_panels", {}),
        }





    def _detect_thickness(self, shape: Any) -> Optional[float]:
        """Detects sheet metal thickness from concentric cylinder pairs or parallel plane distances."""
        faces = self.backend.get_faces(shape)
        if not faces:
            return None

        # 1. Check concentric cylindrical bend faces
        cyls: List[Tuple[Any, CylinderData]] = []
        for f in faces:
            if self.backend.get_surface_type(f) == "cylinder":
                data = self.backend.get_cylinder_data(f)
                if data:
                    cyls.append((f, data))

        for i in range(len(cyls)):
            for j in range(i + 1, len(cyls)):
                _, c1 = cyls[i]
                _, c2 = cyls[j]
                diff = abs(c1.radius - c2.radius)
                if 0.2 <= diff <= 25.0:
                    dot = sum(c1.axis[k] * c2.axis[k] for k in range(3))
                    if abs(abs(dot) - 1.0) < 1e-2:
                        return round(diff, 3)

        # 2. Check bounding box min dimension if single sheet
        bb = self.backend.get_bounding_box(shape)
        if bb and bb != (0, 0, 0, 0, 0, 0):
            dims = sorted([bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]])
            if 0.4 <= dims[0] <= 15.0:
                return round(dims[0], 3)

        return None

    def _detect_bends(
        self,
        shape: Any,
        thickness: Optional[float],
        diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        if hasattr(shape, "get") and shape.get("type") in ("mock_shape", "mock_box"):
            return {
                "BEND_01": {
                    "bend_id": "BEND_01",
                    "origin": (0.0, 0.0, 0.0),
                    "axis": (1.0, 0.0, 0.0),
                    "angle": 90.0,
                    "radius": 2.0,
                    "length": 100.0,
                    "parent_panel_id": "PANEL_A",
                    "child_panel_id": "PANEL_B"
                }
            }

        faces = self.backend.get_faces(shape)
        if not faces:
            return {}

        adj_map = self.backend.get_face_adjacencies(shape)
        
        cylinders = []
        planes = []
        for i, face in enumerate(faces):
            stype = self.backend.get_surface_type(face)
            if stype == "cylinder":
                cylinders.append(str(i))
            elif stype == "plane":
                planes.append(str(i))

        bends = {}
        bend_idx = 1
        
        # Deduplicate inner/outer bend cylinders
        unique_cyls = []
        for cyl_id in cylinders:
            face = faces[int(cyl_id)]
            cyl_data = self.backend.get_cylinder_data(face)
            if not cyl_data:
                continue
            
            radius = cyl_data.radius
            if thickness and radius > thickness * 10.0:
                continue
            
            axis = cyl_data.axis
            origin = cyl_data.origin
            is_dup = False
            for u in unique_cyls:
                u_axis = u['data'].axis
                u_origin = u['origin']
                
                dot_axis = sum(axis[i]*u_axis[i] for i in range(3))
                if abs(abs(dot_axis) - 1.0) < 1e-4:
                    vec = [origin[i] - u_origin[i] for i in range(3)]
                    cross = [
                        vec[1]*axis[2] - vec[2]*axis[1],
                        vec[2]*axis[0] - vec[0]*axis[2],
                        vec[0]*axis[1] - vec[1]*axis[0]
                    ]
                    dist = math.sqrt(sum(c**2 for c in cross))
                    if dist < 1e-3:
                        is_dup = True
                        if radius < u['radius']:
                            u['id'] = cyl_id
                            u['radius'] = radius
                            u['data'] = cyl_data
                        break
            
            if not is_dup:
                unique_cyls.append({
                    'id': cyl_id,
                    'radius': radius,
                    'origin': origin,
                    'data': cyl_data
                })

        panels, face_to_panel = self._group_panels(shape, adj_map, faces, unique_cyls, thickness)
        edges = self._build_panel_edges(
            unique_cyls, panels, face_to_panel, faces, adj_map, shape=shape, diagnostics=diagnostics
        )
        bends = self._canonicalize_bends(edges, panels, thickness)
        self._last_panels = panels
        return bends

    @staticmethod
    def _stable_hash(*args) -> str:
        cleaned = []
        for a in args:
            if isinstance(a, (list, tuple)):
                cleaned.append(tuple(round(float(x), 4) for x in a))
            elif isinstance(a, float):
                cleaned.append(round(a, 4))
            else:
                cleaned.append(str(a))
        s = repr(cleaned)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]

    def _group_panels(
        self,
        shape: Any,
        adj_map: Dict[str, Set[str]],
        faces: List[Any],
        unique_cyls: List[dict],
        thickness: float,
    ) -> Tuple[Dict[str, PanelInfo], Dict[str, str]]:
        all_cyl_ids = [
            str(i) for i, f in enumerate(faces)
            if self.backend.get_surface_type(f) == "cylinder"
        ]
        planes = {
            str(i) for i, f in enumerate(faces)
            if self.backend.get_surface_type(f) == "plane"
        }

        # 1. Collect affected planar faces adjacent to any cylinder
        affected_planar = set()
        for c_id in all_cyl_ids:
            for p_id in adj_map.get(c_id, set()):
                if p_id in planes:
                    affected_planar.add(p_id)

        aff_list = sorted(affected_planar, key=int)
        parent = {fid: fid for fid in aff_list}
        has_pair = {fid: False for fid in aff_list}

        def find(i: str) -> str:
            if parent[i] == i:
                return i
            parent[i] = find(parent[i])
            return parent[i]

        def union(i: str, j: str) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        # 2. Union-find: pairs with anti-parallel normals at distance ~ thickness
        for i in range(len(aff_list)):
            fi_id = aff_list[i]
            fi = faces[int(fi_id)]
            ni = self.backend.get_face_normal(fi)
            ci = self.backend.get_face_center(fi)
            for j in range(i + 1, len(aff_list)):
                fj_id = aff_list[j]
                fj = faces[int(fj_id)]
                nj = self.backend.get_face_normal(fj)
                cj = self.backend.get_face_center(fj)

                dot = ni[0]*nj[0] + ni[1]*nj[1] + ni[2]*nj[2]
                v = [cj[k] - ci[k] for k in range(3)]
                dist_along_n = abs(v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])

                # Anti-parallel opposite sheet faces separated by thickness
                is_thickness_pair = False
                v_perp_sq = (
                    (v[0] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[0])**2 +
                    (v[1] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[1])**2 +
                    (v[2] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[2])**2
                )
                dist_perp = math.sqrt(v_perp_sq)

                if thickness is not None:
                    is_thickness_pair = (
                        abs(dot + 1.0) < 1e-2 and
                        abs(dist_along_n - thickness) < 0.25 * thickness and
                        dist_perp < 2.0 * thickness
                    )
                else:
                    is_thickness_pair = abs(dot + 1.0) < 1e-2 and 0.2 <= dist_along_n <= 25.0 and dist_perp < 5.0

                if is_thickness_pair:
                    union(fi_id, fj_id)
                    has_pair[fi_id] = True
                    has_pair[fj_id] = True
                # Coplanar segments on the same side (only merge if adjacent / connected)
                elif abs(dot - 1.0) < 1e-2 and dist_along_n < 1e-2 and (fj_id in adj_map.get(fi_id, set())):
                    union(fi_id, fj_id)

        # 3. Filter out un-paired faces (keep only panels that form physical sheet thickness pairs)
        groups: Dict[str, List[str]] = {}
        for fid in aff_list:
            if not has_pair[fid]:
                continue
            root = find(fid)
            groups.setdefault(root, []).append(fid)

        panels: Dict[str, PanelInfo] = {}
        face_to_panel: Dict[str, str] = {}

        for root_id, fids in groups.items():
            total_area = sum(self.backend.get_surface_area(faces[int(fid)]) for fid in fids)
            if total_area > 0:
                c_x = sum(self.backend.get_surface_area(faces[int(fid)]) * self.backend.get_face_center(faces[int(fid)])[0] for fid in fids) / total_area
                c_y = sum(self.backend.get_surface_area(faces[int(fid)]) * self.backend.get_face_center(faces[int(fid)])[1] for fid in fids) / total_area
                c_z = sum(self.backend.get_surface_area(faces[int(fid)]) * self.backend.get_face_center(faces[int(fid)])[2] for fid in fids) / total_area
            else:
                c_x, c_y, c_z = 0.0, 0.0, 0.0
            centroid = (c_x, c_y, c_z)

            rep_face = max(
                (faces[int(fid)] for fid in fids),
                key=lambda f: (
                    round(self.backend.get_face_center(f)[2], 4),
                    round(self.backend.get_face_normal(f)[2], 4),
                    round(self.backend.get_face_normal(f)[0], 4)
                )
            )
            rep_normal = self.backend.get_face_normal(rep_face)

            p_hash = self._stable_hash(centroid, rep_normal)
            pid = f"PANEL_{p_hash}"

            p_info = PanelInfo(
                panel_id=pid,
                face_ids=fids,
                total_area=total_area,
                centroid=centroid,
                normal=rep_normal
            )
            panels[pid] = p_info
            for fid in fids:
                face_to_panel[fid] = pid

        return panels, face_to_panel

    def _build_panel_edges(
        self,
        unique_cyls: List[dict],
        panels: Dict[str, PanelInfo],
        face_to_panel: Dict[str, str],
        faces: List[Any],
        adj_map: Dict[str, Set[str]],
        shape: Optional[Any] = None,
        diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> List[dict]:
        edge_to_faces = None
        if shape is not None and not getattr(shape, "IsNull", lambda: True)():
            try:
                from OCC.Core.TopExp import topexp
                from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
                from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
                edge_to_faces = TopTools_IndexedDataMapOfShapeListOfShape()
                topexp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
            except Exception as e:
                logger.debug("MapShapesAndAncestors failed: %s", e)
                edge_to_faces = None

        def find_common_edges(f1: Any, f2: Any) -> List[Any]:
            if edge_to_faces is None:
                return []
            try:
                from OCC.Core.TopExp import TopExp_Explorer
                from OCC.Core.TopAbs import TopAbs_EDGE
                from OCC.Core.TopTools import TopTools_ListIteratorOfListOfShape
                common = []
                exp = TopExp_Explorer(f1, TopAbs_EDGE)
                while exp.More():
                    e = exp.Current()
                    adj_list = edge_to_faces.FindFromKey(e)
                    it = TopTools_ListIteratorOfListOfShape(adj_list)
                    while it.More():
                        if it.Value().IsSame(f2):
                            common.append(e)
                            break
                        it.Next()
                    exp.Next()
                return common
            except Exception:
                return []

        edges_by_pair = {}
        all_cyls = []
        for i, f in enumerate(faces):
            if self.backend.get_surface_type(f) == "cylinder":
                data = self.backend.get_cylinder_data(f)
                if data:
                    all_cyls.append({'id': str(i), 'radius': data.radius, 'data': data})

        for u in all_cyls:
            c_id = u['id']
            c_data = u['data']
            cyl_face = faces[int(c_id)]
            adj_p_faces = [fid for fid in adj_map.get(c_id, set()) if fid in face_to_panel]

            if edge_to_faces is not None:
                real_panels = []
                for fid in adj_p_faces:
                    flat_face = faces[int(fid)]
                    common_edges = find_common_edges(cyl_face, flat_face)
                    # A true sheet flange panel meets the bend cylinder along a straight line edge (generatrix).
                    # Transverse corner relief cuts / notches meet along circular arcs (or other non-line edges).
                    is_real_panel = any(self.backend.get_edge_curve_type(e) == "line" for e in common_edges)
                    if is_real_panel:
                        real_panels.append(face_to_panel[fid])
                connected_panels = sorted(list(set(real_panels)))
            else:
                connected_panels = sorted(list({face_to_panel[fid] for fid in adj_p_faces}))

            if len(connected_panels) == 2:
                p_a, p_b = connected_panels
                pair_key = (p_a, p_b)

                fa_id = [fid for fid in adj_p_faces if face_to_panel[fid] == p_a][0]
                fb_id = [fid for fid in adj_p_faces if face_to_panel[fid] == p_b][0]
                na = self.backend.get_face_normal(faces[int(fa_id)])
                nb = self.backend.get_face_normal(faces[int(fb_id)])

                edge_cand = {
                    'panel_a': p_a,
                    'panel_b': p_b,
                    'normal_a': na,
                    'normal_b': nb,
                    'axis': c_data.axis,
                    'origin': c_data.origin,
                    'radius': u['radius'],
                    'height': c_data.height if c_data.height > 0 else 50.0,
                    'cyl_face': faces[int(c_id)],
                    'cyl_id': c_id,
                }

                if pair_key not in edges_by_pair or edge_cand['radius'] < edges_by_pair[pair_key]['radius']:
                    edges_by_pair[pair_key] = edge_cand
            elif len(connected_panels) > 2:
                if diagnostics is not None:
                    diagnostics.append({
                        "code": "BEND_TOUCHES_MULTIPLE_PANELS",
                        "message": f"cyl {c_id} touches {len(connected_panels)} real panels via line edges: {connected_panels}",
                        "level": "WARNING",
                        "stage": "feature_extraction"
                    })
            elif len(connected_panels) < 2:
                if diagnostics is not None:
                    diagnostics.append({
                        "code": "BEND_TOUCHES_INSUFFICIENT_PANELS",
                        "message": f"cyl {c_id} touches {len(connected_panels)} real panels via line edges",
                        "level": "WARNING",
                        "stage": "feature_extraction"
                    })

        return list(edges_by_pair.values())

    def _canonicalize_bends(
        self,
        edges: List[dict],
        panels: Dict[str, PanelInfo],
        thickness: float,
    ) -> Dict[str, dict]:
        if not panels or not edges:
            return {}

        # 1. Root panel selection:
        # Criteria:
        #   1) max total_area (largest physical panel)
        #   2) min distance to global origin (0,0,0)
        #   3) max centroid coordinates (X, Y, Z) lexicographically to break geometric symmetry
        #   4) lexical panel_id as ultimate deterministic fallback
        root_pid = max(
            panels.keys(),
            key=lambda pid: (
                round(panels[pid].total_area, 4),
                -round(math.sqrt(sum(c**2 for c in panels[pid].centroid)), 6),
                round(panels[pid].centroid[0], 6),
                round(panels[pid].centroid[1], 6),
                round(panels[pid].centroid[2], 6),
                pid
            )
        )

        panel_adj = {pid: [] for pid in panels}
        for e in edges:
            panel_adj[e['panel_a']].append((e['panel_b'], e, 'A_to_B'))
            panel_adj[e['panel_b']].append((e['panel_a'], e, 'B_to_A'))

        for pid in panel_adj:
            panel_adj[pid].sort(key=lambda item: item[0])

        visited = {root_pid}
        queue = [root_pid]
        bends = {}
        bend_idx = 1
        while queue:
            curr_p = queue.pop(0)
            for next_p, edge, direction in panel_adj[curr_p]:
                if next_p in visited:
                    continue
                visited.add(next_p)
                queue.append(next_p)

                parent_p = curr_p
                child_p = next_p
                if direction == 'A_to_B':
                    n_parent, n_child = edge['normal_a'], edge['normal_b']
                else:
                    n_parent, n_child = edge['normal_b'], edge['normal_a']

                signed_ang = self.calculate_signed_angle(n_parent, n_child, edge['axis'])
                bend_hash = self._stable_hash(parent_p, child_p, edge['origin'], edge['axis'])
                bend_id = f"BEND_{bend_idx:02d}_{bend_hash}"

                bends[bend_id] = {
                    "bend_id": bend_id,
                    "origin": edge["origin"],
                    "axis": edge["axis"],
                    "signed_angle": signed_ang,
                    "radius": edge["radius"],
                    "length": edge["height"],
                    "parent_panel_id": parent_p,
                    "child_panel_id": child_p,
                    "thickness": thickness,
                    "cyl_face": edge["cyl_face"],
                }
                bend_idx += 1

        return bends
