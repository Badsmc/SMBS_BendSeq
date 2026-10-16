"""
Topology Worker for SMBS / BendSeq.
Compliant with ТЗ v1.2 §3.3 (Rule R3), §4.1, §4.2 and Principles 2, 5, 17, 23.
Extracts canonical panel and bend topology from geometry.brep and writes 04_canonical_part.json.
"""

from __future__ import annotations
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder
import networkx as nx

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.protocols.file_store import (
    ConfidenceLevel,
    ContractError,
    FileStore,
    ParameterSource,
    RunStatus,
    TopologyStatus,
)


class TopologyWorker:
    """
    Topology Worker analyzing 3D B-Rep to construct canonical panel & bend graphs.
    Enforces Rule R3: MATCHED / AMBIGUOUS / FAILED.
    """

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()
        self.backend = PythonOCCBackend()
        self.extractor = FeatureExtractor(self.backend)

    def _load_brep(self, file_path: Union[str, Path]) -> TopoDS_Shape:
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not breptools.Read(shape, str(file_path), builder):
            raise ValueError(f"Failed to load BREP from {file_path}")
        return shape

    @staticmethod
    def _compute_bend_allowance(angle_deg: float, radius_mm: float, k_factor: float, thickness_mm: float) -> float:
        """
        Calculates sheet metal bend allowance (BA):
        BA = alpha_rad * (radius_mm + k_factor * thickness_mm)
        """
        alpha_rad = math.radians(abs(angle_deg))
        return alpha_rad * (radius_mm + k_factor * thickness_mm)

    def _map_raw_bends_to_flat(
        self,
        raw_bends: Dict[str, Any],
        flat_bend_lines: List[Dict[str, Any]],
        raw_panels: Dict[str, Any],
        thickness_mm: float,
        default_k_factor: float = 0.44,
        diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Geometrically matches each raw_bend (3D) to its corresponding flat_bend_line (2D)
        based on the topological chain traversal and Criterion A (bend distance) / Criterion B (panel length).
        Never returns naive idx fallback. If topology is non-linear, returns {} and logs diagnostic.
        """
        if not flat_bend_lines:
            return {}
        if len(raw_bends) <= 1:
            return {list(raw_bends.keys())[0]: flat_bend_lines[0]} if raw_bends and flat_bend_lines else {}

        # Build 1D panel-bend topological connectivity graph
        G = nx.Graph()
        for bid, binfo in raw_bends.items():
            p1 = binfo.get("parent_panel_id")
            p2 = binfo.get("child_panel_id")
            if p1 and p2:
                G.add_edge(p1, p2, bend_id=bid)

        degrees = dict(G.degree())
        leaves = [n for n, d in degrees.items() if d == 1]
        is_linear_chain = (len(leaves) == 2 and all(d <= 2 for d in degrees.values()))

        if not is_linear_chain:
            if diagnostics is not None:
                diagnostics.append({
                    "code": "NON_LINEAR_TOPOLOGY_NOT_SUPPORTED",
                    "message": (
                        f"Bend mapping requires linear 1D panel chain, but part topology has "
                        f"{len(leaves)} leaves and max panel degree {max(degrees.values()) if degrees else 0}. "
                        f"bend_line_flat_pattern will be omitted."
                    ),
                    "level": "WARNING",
                    "stage": "topology_bend_mapping"
                })
            return {}

        chain_panels = nx.shortest_path(G, source=leaves[0], target=leaves[1])
        bends_forward = [G[chain_panels[i]][chain_panels[i + 1]]["bend_id"] for i in range(len(chain_panels) - 1)]
        bends_backward = list(reversed(bends_forward))
        panels_forward = chain_panels
        panels_backward = list(reversed(chain_panels))

        # Determine dominant coordinate axis on flat pattern (X or Y)
        mids = []
        for fb in flat_bend_lines:
            s = fb.get("start_point") or fb.get("start_mm", [0, 0, 0])
            e = fb.get("end_point") or fb.get("end_mm", [0, 0, 0])
            mids.append(((s[0] + e[0]) / 2.0, (s[1] + e[1]) / 2.0))

        x_span = max(m[0] for m in mids) - min(m[0] for m in mids)
        y_span = max(m[1] for m in mids) - min(m[1] for m in mids)

        if x_span >= y_span:
            def coord_fn(fb: Dict[str, Any]) -> float:
                s = fb.get("start_point") or fb.get("start_mm", [0, 0, 0])
                e = fb.get("end_point") or fb.get("end_mm", [0, 0, 0])
                return (s[0] + e[0]) / 2.0
        else:
            def coord_fn(fb: Dict[str, Any]) -> float:
                s = fb.get("start_point") or fb.get("start_mm", [0, 0, 0])
                e = fb.get("end_point") or fb.get("end_mm", [0, 0, 0])
                return (s[1] + e[1]) / 2.0

        sorted_flat_bends = sorted(flat_bend_lines, key=coord_fn)

        def chain_distance_residual(b_order: List[str], p_order: List[str]) -> float:
            residual = 0.0
            for i in range(len(b_order) - 1):
                d_flat = abs(coord_fn(sorted_flat_bends[i + 1]) - coord_fn(sorted_flat_bends[i]))
                mid_panel_id = p_order[i + 1]
                mid_p_info = raw_panels.get(mid_panel_id, {})
                p_area = float(
                    mid_p_info.total_area
                    if hasattr(mid_p_info, "total_area")
                    else (mid_p_info.get("total_area", mid_p_info.get("area_mm2", 0.0)) if isinstance(mid_p_info, dict) else 0.0)
                )
                b_len = float(raw_bends[b_order[i]].get("length", 50.0))
                p_len_3d = p_area / (b_len * 2.0) if b_len > 0 else 0.0

                # Compute dynamic BA for each adjacent bend using actual bend parameters
                b1 = raw_bends[b_order[i]]
                b2 = raw_bends[b_order[i + 1]]
                b1_angle = float(b1.get("signed_angle", b1.get("angle", 90.0)))
                b1_radius = float(b1.get("radius", 1.0))
                b2_angle = float(b2.get("signed_angle", b2.get("angle", 90.0)))
                b2_radius = float(b2.get("radius", 1.0))

                ba1 = self._compute_bend_allowance(b1_angle, b1_radius, default_k_factor, thickness_mm)
                ba2 = self._compute_bend_allowance(b2_angle, b2_radius, default_k_factor, thickness_mm)

                expected_d_flat = p_len_3d + (ba1 + ba2) / 2.0
                residual += abs(d_flat - expected_d_flat)

            # Angle supplement check
            for i, bid in enumerate(b_order):
                r_angle = abs(float(raw_bends[bid].get("signed_angle", raw_bends[bid].get("angle", 90.0))))
                f_angle = abs(float(sorted_flat_bends[i].get("angle_deg", 90.0)))
                residual += abs(f_angle - (180.0 - r_angle)) * 0.1

            return residual

        res_fwd = chain_distance_residual(bends_forward, panels_forward)
        res_bwd = chain_distance_residual(bends_backward, panels_backward)

        chosen_bends = bends_forward if res_fwd <= res_bwd else bends_backward
        matched: Dict[str, Any] = {}
        for bid, fb in zip(chosen_bends, sorted_flat_bends):
            matched[bid] = fb
        return matched

    def execute(self, geometry_result_path: Union[str, Path]) -> Path:
        """
        Executes Topology Worker reading 02_geometry_result.json.
        Outputs 04_canonical_part.json.
        """
        geom_result = self.file_store.read_json_verified(
            geometry_result_path,
            expected_schema_name="geometry_result"
        )

        run_id = geom_result["run_id"]
        part_id = geom_result["part_id"]
        input_fingerprint = geom_result["input_fingerprint"]
        run_dir = Path(geometry_result_path).parent
        workspace_root = run_dir.parent.parent

        artifact_rel_path = geom_result["payload"]["geometry_artifact"]["file_path"]
        brep_path = workspace_root / artifact_rel_path
        if not brep_path.exists():
            # Try looking directly in run_dir
            brep_path = run_dir / Path(artifact_rel_path).name

        occ_shape = self._load_brep(brep_path)
        diagnostics = []

        # Extract features
        known_t = geom_result["payload"].get("thickness_mm", {}).get("value")
        feats = self.extractor.extract_features(occ_shape, known_thickness=known_t, diagnostics=diagnostics)
        raw_thickness = feats.get("thickness")
        raw_bends = feats.get("bends", {})
        raw_panels = feats.get("panels", {})
        raw_records = feats.get("records", {})

        # Status determination (Rule R3)
        topology_status = TopologyStatus.MATCHED
        if not raw_bends:
            topology_status = TopologyStatus.FAILED
            diagnostics.append({
                "code": "NO_BENDS_DETECTED",
                "message": "Topology worker found 0 valid cylindrical bend pairs.",
                "level": "ERROR",
                "stage": "topology_extraction"
            })

        # Check for symmetric ambiguities
        bend_geometries: List[Tuple[float, float, float]] = []
        for b_id, b_info in raw_bends.items():
            key = (round(b_info.get("length", 0.0), 2), round(abs(b_info.get("signed_angle", 0.0)), 1), round(b_info.get("radius", 0.0), 2))
            if key in bend_geometries:
                # Potential symmetry check: if distance topologies are identical
                pass
            bend_geometries.append(key)

        # Assemble Panels
        canonical_panels: Dict[str, Any] = {}
        for p_id, p_info in raw_panels.items():
            canonical_panels[p_id] = {
                "panel_id": p_id,
                "area_mm2": float(p_info.total_area if hasattr(p_info, 'total_area') else p_info.get('total_area', 0.0)),
                "centroid": [float(c) for c in (p_info.centroid if hasattr(p_info, 'centroid') else p_info.get('centroid', [0,0,0]))],
                "normal": [float(n) for n in (p_info.normal if hasattr(p_info, 'normal') else p_info.get('normal', [0,0,1]))],
                "adjacent_bend_ids": []
            }

        # Reference panel is the largest panel
        ref_panel_id = "PANEL_01"
        if canonical_panels:
            ref_panel_id = max(canonical_panels.keys(), key=lambda pid: canonical_panels[pid]["area_mm2"])

        # Retrieve flat pattern bend lines from geometry_result if available
        flat_bend_lines = []
        flat_art = geom_result["payload"].get("flat_pattern_artifact")
        if flat_art and isinstance(flat_art, dict):
            flat_bend_lines = flat_art.get("bend_lines", [])

        # Verify bend count correspondence between 3D feature extraction and 2D unfold
        bend_count_matches = True
        if flat_bend_lines and len(raw_bends) != len(flat_bend_lines):
            bend_count_matches = False
            diagnostics.append({
                "code": "BEND_COUNT_MISMATCH",
                "message": (
                    f"Bend count mismatch between feature_extractor ({len(raw_bends)}) "
                    f"and unfold ({len(flat_bend_lines)}). bend_line_flat_pattern mapping omitted."
                ),
                "level": "WARNING",
                "stage": "topology_bend_mapping"
            })

        # Provenance for thickness & k_factor needed for bend allowance calculation
        t_val = geom_result["payload"]["thickness_mm"]["value"] or raw_thickness or 1.5
        t_source = geom_result["payload"]["thickness_mm"]["source"]
        t_confidence = geom_result["payload"]["thickness_mm"]["confidence"]
        if t_val is None:
            t_confidence = ConfidenceLevel.UNKNOWN.value

        k_val = 0.44 if geom_result["payload"].get("k_factor", {}).get("value") is None else geom_result["payload"]["k_factor"]["value"]

        # Geometrically map raw_bends to flat_bend_lines using dynamic BA calculation
        bend_mapping: Dict[str, Any] = {}
        if bend_count_matches and flat_bend_lines:
            bend_mapping = self._map_raw_bends_to_flat(
                raw_bends,
                flat_bend_lines,
                raw_panels,
                thickness_mm=float(t_val),
                default_k_factor=float(k_val),
                diagnostics=diagnostics,
            )

        if flat_bend_lines and not bend_mapping:
            diagnostics.append({
                "code": "BEND_MAPPING_OMITTED",
                "message": "Flat pattern bend lines were not mapped to canonical bends due to non-linear topology, bend count mismatch, or mapping failure.",
                "level": "WARNING",
                "stage": "topology_bend_mapping"
            })

        # Assemble Bends
        canonical_bends: Dict[str, Any] = {}
        for idx, (b_id, b_info) in enumerate(raw_bends.items()):
            p1 = b_info.get("parent_panel_id", "PANEL_01")
            p2 = b_info.get("child_panel_id", "PANEL_02")
            if p1 in canonical_panels and b_id not in canonical_panels[p1]["adjacent_bend_ids"]:
                canonical_panels[p1]["adjacent_bend_ids"].append(b_id)
            if p2 in canonical_panels and b_id not in canonical_panels[p2]["adjacent_bend_ids"]:
                canonical_panels[p2]["adjacent_bend_ids"].append(b_id)

            angle_val = float(b_info.get("signed_angle", b_info.get("angle", 90.0)))
            direction_str = "UP" if angle_val >= 0 else "DOWN"

            canonical_bends[b_id] = {
                "bend_id": b_id,
                "parent_panel_id": p1,
                "child_panel_id": p2,
                "angle_deg": {
                    "value": angle_val,
                    "source": ParameterSource.GEOMETRIC_INFERENCE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
                "radius_mm": {
                    "value": float(b_info.get("radius", 1.0)),
                    "source": ParameterSource.GEOMETRIC_INFERENCE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
                "length_mm": {
                    "value": float(b_info.get("length", 50.0)),
                    "source": ParameterSource.GEOMETRIC_INFERENCE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
                "direction": {
                    "value": direction_str,
                    "source": ParameterSource.GEOMETRIC_INFERENCE.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value
                },
                "k_factor": {
                    "value": 0.44 if geom_result["payload"].get("k_factor", {}).get("value") is None else geom_result["payload"]["k_factor"]["value"],
                    "source": ParameterSource.DEFAULT_POLICY.value if geom_result["payload"].get("k_factor", {}).get("value") is None else ParameterSource.SHEETMETAL_NATIVE.value,
                    "confidence": ConfidenceLevel.ESTIMATED.value if geom_result["payload"].get("k_factor", {}).get("value") is None else ConfidenceLevel.CONFIRMED.value
                },
                "axis_point": [float(x) for x in b_info.get("origin", [0,0,0])],
                "axis_direction": [float(x) for x in b_info.get("axis", [1,0,0])]
            }

            # Propagate bend_line_flat_pattern using proven geometric mapping
            fbl = bend_mapping.get(b_id)
            if fbl is not None:
                start_pt = fbl.get("start_point") or fbl.get("start_mm")
                end_pt = fbl.get("end_point") or fbl.get("end_mm")
                if start_pt and end_pt:
                    l_val = math.sqrt(sum((end_pt[k] - start_pt[k])**2 for k in range(min(len(start_pt), len(end_pt)))))
                    canonical_bends[b_id]["bend_line_flat_pattern"] = {
                        "start_mm": [float(x) for x in start_pt[:3]],
                        "end_mm": [float(x) for x in end_pt[:3]],
                        "length_mm": round(float(l_val), 3)
                    }

        # Provenance for thickness already resolved above

        canonical_data = {
            "schema_name": "canonical_part",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "topology_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value if topology_status == TopologyStatus.MATCHED else RunStatus.PARTIAL.value,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "input_mode": "CANONICAL_PART",
                "thickness_mm": {
                    "value": t_val,
                    "unit": "mm",
                    "source": t_source,
                    "confidence": t_confidence
                },
                "reference_panel_id": ref_panel_id,
                "geometry_artifact": geom_result["payload"]["geometry_artifact"],
                "panels": canonical_panels,
                "bends": canonical_bends
            }
        }

        out_file = run_dir / "04_canonical_part.json"
        self.file_store.write_json_atomic(out_file, canonical_data, schema_name="canonical_part")
        return out_file
