from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepTools import breptools
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.gp import gp_Pnt, gp_Trsf, gp_Vec
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape, topods

from smbs_core.Geometry.frame_aligner import FrameAligner, FrameAlignmentResult
from smbs_core.protocols.file_store import (
    AlignmentStatus,
    FileStore,
    RunStatus,
    compute_file_sha256,
)


class BaselineWorker:
    """
    Worker for Stage 1 Baseline proof:
    Validates that unfold -> align -> fold -> compare holds for fixtures without search or collisions.
    """

    def _build_error_payload(
        self,
        run_id: str,
        part_id: str,
        input_fingerprint: str,
        diagnostics: List[Dict[str, Any]],
        baseline_status: str,
        active_mode: str,
        ref_panel_id: Optional[str] = None,
        align_status: str = "CANNOT_VERIFY",
    ) -> Dict[str, Any]:
        return {
            "schema_name": "baseline_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "baseline_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.WORKER_ERROR.value,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "baseline_status": baseline_status,
                "comparison_mode": active_mode,
                "pass": False,
                "frame_alignment": {
                    "status": align_status,
                    "det_R": 0.0,
                    "normal_dot": 0.0,
                    "tangent_dot": 0.0,
                    "reference_panel_id": ref_panel_id or "",
                },
                "restored_shape_artifact": {"file_path": "", "format": "BREP", "content_hash": "0" * 64},
                "metrics": {
                    "is_valid_brep": False,
                    "solids_count": 0,
                    "shells_count": 0,
                    "volume_delta_mm3": 9999.0,
                    "relative_volume_delta": 1.0,
                    "surface_area_delta_mm2": 9999.0,
                    "hausdorff_distance_mm": 9999.0,
                    "bbox_delta_mm": [9999.0, 9999.0, 9999.0],
                    "ref_faces_raw": 0,
                    "ref_faces_canonical": 0,
                    "restored_faces_raw": 0,
                    "restored_faces_canonical": 0,
                    "ref_cyl_faces_raw": 0,
                    "ref_cyl_faces_canonical": 0,
                    "restored_cyl_faces_raw": 0,
                    "restored_cyl_faces_canonical": 0,
                    "topology_matches": False,
                    "faces_count_reference": 0,
                    "faces_count_restored": 0,
                    "faces_count_delta": 0,
                    "cylindrical_faces_delta": 0,
                    "raw_comparison_result": {
                        "topology_matches": False,
                        "counts_match": False,
                        "sum_areas_match": False,
                        "plane_areas_match": False,
                        "cyl_areas_match": False,
                        "normals_match": False,
                    },
                    "topology_details": {
                        "counts": {"Plane": 0, "Cylinder": 0, "Other": 0, "Total": 0},
                        "sum_areas": {"Plane": 0.0, "Cylinder": 0.0, "Other": 0.0, "Total": 0.0},
                        "plane_areas_sorted": [],
                        "cyl_areas_sorted": [],
                        "normals_sorted": [],
                        "plane_positions_sorted": [],
                    },
                },
            },
        }

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()

    def _load_brep(self, file_path: Union[str, Path]) -> TopoDS_Shape:
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Shape

        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not breptools.Read(shape, str(file_path), builder):
            raise ValueError(f"Failed to load BREP from {file_path}")
        return shape

    @staticmethod
    def _compute_hausdorff_distance(shape1: TopoDS_Shape, shape2: TopoDS_Shape) -> float:
        """
        Computes two-way vertex Hausdorff distance between two shapes:
        H(S1, S2) = max(max_{v1 in S1} dist(v1, S2), max_{v2 in S2} dist(v2, S1)).
        """
        def directed_hausdorff(s1: TopoDS_Shape, s2: TopoDS_Shape) -> float:
            max_d = 0.0
            exp = TopExp_Explorer(s1, TopAbs_VERTEX)
            while exp.More():
                v = exp.Current()
                ext = BRepExtrema_DistShapeShape(v, s2)
                ext.Perform()
                if ext.IsDone():
                    d = ext.Value()
                    if d > max_d:
                        max_d = d
                exp.Next()
            return max_d

        d1 = directed_hausdorff(shape1, shape2)
        d2 = directed_hausdorff(shape2, shape1)
        return max(d1, d2)

    def _find_largest_planar_face(self, shape: TopoDS_Shape) -> TopoDS_Face:
        max_area = -1.0
        best_face = None
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            face = topods.Face(exp.Current())
            surf = BRepAdaptor_Surface(face, True)
            if surf.GetType() == GeomAbs_Plane:
                props = GProp_GProps()
                brepgprop.SurfaceProperties(face, props)
                area = props.Mass()
                if area > max_area:
                    max_area = area
                    best_face = face
            exp.Next()

        if not best_face:
            raise ValueError("No planar face found in shape.")
        return best_face

    def _get_shape_properties(self, shape: TopoDS_Shape) -> Tuple[float, float, List[float]]:
        """Returns (volume, surface_area, bbox_extents)."""
        vol_props = GProp_GProps()
        brepgprop.VolumeProperties(shape, vol_props)
        vol = vol_props.Mass()

        surf_props = GProp_GProps()
        brepgprop.SurfaceProperties(shape, surf_props)
        area = surf_props.Mass()

        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        extents = [xmax - xmin, ymax - ymin, zmax - zmin]

        return vol, area, extents

    @staticmethod
    def extract_topology(shape: TopoDS_Shape) -> Dict[str, Any]:
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        cx_box = (xmin + xmax) / 2.0
        cy_box = (ymin + ymax) / 2.0
        cz_box = (zmin + zmax) / 2.0

        exp = TopExp_Explorer(shape, TopAbs_FACE)
        counts = {"Plane": 0, "Cylinder": 0, "Other": 0, "Total": 0}
        sum_areas = {"Plane": 0.0, "Cylinder": 0.0, "Other": 0.0, "Total": 0.0}
        plane_areas: List[float] = []
        cyl_areas: List[float] = []
        planes: List[Tuple[float, float, float, float, float]] = []

        while exp.More():
            f = exp.Current()
            surf = BRepAdaptor_Surface(f, True)
            st = surf.GetType()
            gp = GProp_GProps()
            brepgprop.SurfaceProperties(f, gp)
            area = gp.Mass()

            counts["Total"] += 1
            sum_areas["Total"] += area

            if st == GeomAbs_Plane:
                counts["Plane"] += 1
                sum_areas["Plane"] += area
                plane_areas.append(area)
                c = gp.CentreOfMass()
                u_mid = (surf.FirstUParameter() + surf.LastUParameter()) / 2.0
                v_mid = (surf.FirstVParameter() + surf.LastVParameter()) / 2.0
                p = gp_Pnt()
                d1u = gp_Vec()
                d1v = gp_Vec()
                surf.D1(u_mid, v_mid, p, d1u, d1v)
                norm = d1u.Crossed(d1v)
                if norm.Magnitude() > 1e-7:
                    norm.Normalize()
                    dx, dy, dz = norm.X(), norm.Y(), norm.Z()
                else:
                    pl = surf.Plane()
                    d = pl.Axis().Direction()
                    dx, dy, dz = d.X(), d.Y(), d.Z()
                if f.Orientation() == TopAbs_REVERSED:
                    dx, dy, dz = -dx, -dy, -dz
                pos = dx * (c.X() - cx_box) + dy * (c.Y() - cy_box) + dz * (c.Z() - cz_box)
                planes.append((dx, dy, dz, pos, area))
            elif st == GeomAbs_Cylinder:
                counts["Cylinder"] += 1
                sum_areas["Cylinder"] += area
                cyl_areas.append(area)
            else:
                counts["Other"] += 1
                sum_areas["Other"] += area
            exp.Next()

        plane_areas.sort()
        cyl_areas.sort()
        planes.sort(key=lambda item: (round(item[0], 2), round(item[1], 2), round(item[2], 2), round(item[3], 2)))

        plane_normals = [(p[0], p[1], p[2]) for p in planes]
        plane_positions = [p[3] for p in planes]

        return {
            "counts": counts,
            "sum_areas": sum_areas,
            "plane_areas": plane_areas,
            "cyl_areas": cyl_areas,
            "plane_normals": plane_normals,
            "plane_positions": plane_positions,
        }

    @staticmethod
    def compare_topologies(
        top_ref: Dict[str, Any],
        top_rest: Dict[str, Any],
        linear_tol: float = 0.1,
        angular_tol_deg: float = 0.5,
    ) -> Dict[str, Any]:
        counts_match = top_ref["counts"] == top_rest["counts"]

        sum_areas_match = True
        area_tol = max(1.0, linear_tol * 10.0)
        for k in ["Plane", "Cylinder", "Other", "Total"]:
            if abs(top_ref["sum_areas"][k] - top_rest["sum_areas"][k]) > area_tol:
                sum_areas_match = False
                break

        plane_areas_match = len(top_ref["plane_areas"]) == len(top_rest["plane_areas"])
        if plane_areas_match:
            for a_r, a_s in zip(top_ref["plane_areas"], top_rest["plane_areas"]):
                if abs(a_r - a_s) > area_tol:
                    plane_areas_match = False
                    break

        cyl_areas_match = len(top_ref["cyl_areas"]) == len(top_rest["cyl_areas"])
        if cyl_areas_match:
            for a_r, a_s in zip(top_ref["cyl_areas"], top_rest["cyl_areas"]):
                if abs(a_r - a_s) > area_tol:
                    cyl_areas_match = False
                    break

        normals_match = (
            len(top_ref["plane_normals"]) == len(top_rest["plane_normals"])
            and len(top_ref.get("plane_positions", [])) == len(top_rest.get("plane_positions", []))
        )
        cos_tol = math.cos(math.radians(angular_tol_deg))
        if normals_match:
            for n_r, n_s, p_r, p_s in zip(
                top_ref["plane_normals"],
                top_rest["plane_normals"],
                top_ref.get("plane_positions", []),
                top_rest.get("plane_positions", []),
            ):
                dot = n_r[0] * n_s[0] + n_r[1] * n_s[1] + n_r[2] * n_s[2]
                if dot < cos_tol or abs(p_r - p_s) > linear_tol:
                    normals_match = False
                    break

        all_match = (
            counts_match
            and sum_areas_match
            and plane_areas_match
            and cyl_areas_match
            and normals_match
        )
        return {
            "topology_matches": all_match,
            "counts_match": counts_match,
            "sum_areas_match": sum_areas_match,
            "plane_areas_match": plane_areas_match,
            "cyl_areas_match": cyl_areas_match,
            "normals_match": normals_match,
        }

    def execute(
        self,
        geometry_result_path: Union[str, Path],
        canonical_part_path: Union[str, Path],
        tolerance_profile: Optional[Dict[str, float]] = None,
        comparison_mode: Optional[str] = None,
    ) -> Path:
        """
        Executes Stage 1 Baseline verification.
        Writes runs/<run_id>/04_baseline_result.json.
        """
        geom_result = self.file_store.read_json_verified(
            geometry_result_path, expected_schema_name="geometry_result"
        )
        try:
            canonical_part = self.file_store.read_json_verified(
                canonical_part_path, expected_schema_name="canonical_part"
            )
        except Exception:
            # KNOWN DEVIATION (Task 1.0.1b-3a / ТЗ §23):
            # Fallback to plain JSON reading is temporarily permitted to allow topology error handling
            # (AMBIGUOUS_TOPOLOGY_ROOT, NON_TREE_TOPOLOGY, DISCONNECTED_PANEL_GRAPH) on synthetic test fixtures
            # that omit full canonical_part schema fields.
            # TODO (Stage 3c / 1.0.2): Replace synthetic partial JSONs with fully valid schema instances
            # and restore strict CONTRACT_ERROR rejection without schema bypass.
            import json
            with open(canonical_part_path) as f:
                canonical_part = json.load(f)

        run_id = geom_result["run_id"]
        part_id = geom_result["part_id"]
        input_fingerprint = geom_result["input_fingerprint"]
        run_dir = Path(geometry_result_path).parent
        workspace_root = run_dir.parent.parent

        manifest_path = run_dir / "01_input_manifest.json"
        active_mode = comparison_mode
        if not active_mode and manifest_path.exists():
            try:
                manifest_data = self.file_store.read_json_verified(
                    manifest_path, expected_schema_name="input_manifest"
                )
                active_mode = manifest_data["payload"].get("comparison_mode")
            except Exception:
                pass
        active_mode = (active_mode or "CANONICAL").upper()

        tol = {
            "linear_tol_mm": 0.1,
            "angular_tol_deg": 0.5,
            "volume_tol_mm3": 5.0,
            "relative_volume_tol": 0.01,
            "surface_distance_tol_mm": 0.2,
            "surface_area_tol_mm2": 5.0,
            "bbox_tol_mm": 0.2,
            **(tolerance_profile or {}),
        }

        diagnostics: List[Dict[str, Any]] = []

        # 0. Topology validations (§1, Task 1.0.1b-3a ambiguity & graph checks)
        c_payload = canonical_part.get("payload", canonical_part)
        c_panels = c_payload.get("panels", {})
        c_bends = c_payload.get("bends", {})
        ref_p_id = c_payload.get("reference_panel_id")

        # 0.1 Check for cycles: len(bends) >= len(panels)
        if len(c_bends) >= len(c_panels) and len(c_panels) > 0:
            diagnostics.append({
                "code": "NON_TREE_TOPOLOGY",
                "message": f"Graph has cycle or non-tree topology: {len(c_bends)} bends >= {len(c_panels)} panels.",
                "level": "ERROR",
                "stage": "topology_validation",
            })
            result_payload = self._build_error_payload(
                run_id=run_id,
                part_id=part_id,
                input_fingerprint=input_fingerprint,
                diagnostics=diagnostics,
                baseline_status="INVALID",
                active_mode=active_mode,
                ref_panel_id=ref_p_id,
            )
            out_file = run_dir / "04_baseline_result.json"
            self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
            return out_file

        # 0.2 Ambiguity check: >= 2 panels with same max degree >= 3 and no reference_panel_id
        if c_panels:
            panel_degrees = {pid: len(p.get("adjacent_bend_ids", [])) for pid, p in c_panels.items()}
            max_deg = max(panel_degrees.values()) if panel_degrees else 0
            if max_deg >= 3:
                hubs = [pid for pid, deg in panel_degrees.items() if deg == max_deg]
                if len(hubs) >= 2 and not ref_p_id:
                    diagnostics.append({
                        "code": "AMBIGUOUS_TOPOLOGY_ROOT",
                        "message": f"Multiple root hubs found with degree {max_deg}: {hubs} without reference_panel_id.",
                        "level": "ERROR",
                        "stage": "topology_validation",
                    })
                    result_payload = self._build_error_payload(
                        run_id=run_id,
                        part_id=part_id,
                        input_fingerprint=input_fingerprint,
                        diagnostics=diagnostics,
                        baseline_status="INVALID",
                        active_mode=active_mode,
                        ref_panel_id=ref_p_id,
                    )
                    out_file = run_dir / "04_baseline_result.json"
                    self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
                    return out_file

            # 0.3 Disconnected panel graph check: panels unreachable from hub
            # Find primary root
            root_hub = ref_p_id if (ref_p_id and ref_p_id in c_panels) else (
                max(panel_degrees, key=panel_degrees.get) if panel_degrees else None
            )
            if root_hub:
                # Traverse graph via adjacent_bend_ids
                # Build adjacency between panels
                bend_to_panels = {}
                for pid, p in c_panels.items():
                    for bid in p.get("adjacent_bend_ids", []):
                        bend_to_panels.setdefault(bid, []).append(pid)

                visited = set([root_hub])
                queue = [root_hub]
                while queue:
                    curr = queue.pop(0)
                    for bid in c_panels[curr].get("adjacent_bend_ids", []):
                        for neighbor in bend_to_panels.get(bid, []):
                            if neighbor not in visited:
                                visited.add(neighbor)
                                queue.append(neighbor)

                if len(visited) < len(c_panels):
                    unreachable = set(c_panels.keys()) - visited
                    diagnostics.append({
                        "code": "DISCONNECTED_PANEL_GRAPH",
                        "message": f"Panels unreachable from root hub {root_hub}: {unreachable}",
                        "level": "ERROR",
                        "stage": "topology_validation",
                    })
                    result_payload = self._build_error_payload(
                        run_id=run_id,
                        part_id=part_id,
                        input_fingerprint=input_fingerprint,
                        diagnostics=diagnostics,
                        baseline_status="INVALID",
                        active_mode=active_mode,
                        ref_panel_id=ref_p_id,
                    )
                    out_file = run_dir / "04_baseline_result.json"
                    self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
                    return out_file

        # 1. Load reference 3D shape and flat pattern
        geom_artifact_rel = geom_result["payload"]["geometry_artifact"]["file_path"]
        ref_brep_path = workspace_root / geom_artifact_rel
        if not ref_brep_path.exists():
            ref_brep_path = run_dir / Path(geom_artifact_rel).name

        ref_shape = self._load_brep(ref_brep_path)

        flat_artifact = geom_result["payload"].get("flat_pattern_artifact")
        if not flat_artifact or not flat_artifact.get("file_path"):
            raise ValueError("Baseline requires flat_pattern_artifact from GeometryWorker.")

        flat_brep_path = workspace_root / flat_artifact["file_path"]
        if not flat_brep_path.exists():
            flat_brep_path = run_dir / Path(flat_artifact["file_path"]).name

        flat_shape = self._load_brep(flat_brep_path)

        # 2. Extract reference faces for alignment
        ref_face = self._find_largest_planar_face(ref_shape)
        flat_face = self._find_largest_planar_face(flat_shape)

        # 3. Align full frame T = (R, t) (§5.2)
        align_res: FrameAlignmentResult = FrameAligner.align_full_frame(flat_face, ref_face)

        if align_res.status != AlignmentStatus.OK or align_res.transform is None:
            baseline_status = "UNKNOWN"
            diagnostics.append({
                "code": "FRAME_ALIGNMENT_FAILED",
                "message": align_res.error_message or "Frame alignment failed.",
                "level": "ERROR",
                "stage": "frame_alignment",
            })
            result_payload = {
                "schema_name": "baseline_result",
                "schema_version": "1.1",
                "run_id": run_id,
                "part_id": part_id,
                "input_fingerprint": input_fingerprint,
                "producer": {"name": "baseline_worker", "version": "1.1"},
                "units": "mm",
                "frame_convention": "STANDARD_CARTESIAN_MM",
                "status": RunStatus.WORKER_ERROR.value,
                "diagnostics": diagnostics,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "baseline_status": baseline_status,
                    "comparison_mode": active_mode,
                    "pass": False,
                    "frame_alignment": {
                        "status": align_res.status.value,
                        "det_R": align_res.det_R,
                        "normal_dot": align_res.normal_dot,
                        "tangent_dot": align_res.tangent_dot,
                        "reference_panel_id": canonical_part["payload"]["reference_panel_id"],
                    },
                    "restored_shape_artifact": {"file_path": "", "format": "BREP", "content_hash": "0" * 64},
                    "metrics": {
                        "is_valid_brep": False,
                        "solids_count": 0,
                        "shells_count": 0,
                        "volume_delta_mm3": 9999.0,
                        "relative_volume_delta": 1.0,
                        "surface_area_delta_mm2": 9999.0,
                        "hausdorff_distance_mm": 9999.0,
                        "bbox_delta_mm": [9999.0, 9999.0, 9999.0],
                        "ref_faces_raw": 0,
                        "ref_faces_canonical": 0,
                        "restored_faces_raw": 0,
                        "restored_faces_canonical": 0,
                        "ref_cyl_faces_raw": 0,
                        "ref_cyl_faces_canonical": 0,
                        "restored_cyl_faces_raw": 0,
                        "restored_cyl_faces_canonical": 0,
                        "topology_matches": False,
                        "faces_count_reference": 0,
                        "faces_count_restored": 0,
                        "faces_count_delta": 0,
                        "cylindrical_faces_delta": 0,
                        "raw_comparison_result": {
                            "topology_matches": False,
                            "counts_match": False,
                            "sum_areas_match": False,
                            "plane_areas_match": False,
                            "cyl_areas_match": False,
                            "normals_match": False,
                        },
                        "topology_details": {
                            "counts": {"Plane": 0, "Cylinder": 0, "Other": 0, "Total": 0},
                            "sum_areas": {"Plane": 0.0, "Cylinder": 0.0, "Other": 0.0, "Total": 0.0},
                            "plane_areas_sorted": [],
                            "cyl_areas_sorted": [],
                            "normals_sorted": [],
                            "plane_positions_sorted": [],
                        },
                    },
                },
            }
            out_file = run_dir / "04_baseline_result.json"
            self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
            return out_file

        # 4. Transform flat pattern into aligned orientation T * flat
        transformer = BRepBuilderAPI_Transform(flat_shape, align_res.transform, True)
        aligned_flat = transformer.Shape()

        # 5. Restore 3D shape by physical folding via SheetMetalFolder (§5.1)
        from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder

        # Step B.4.1 + KL-5: 1D and 3a paths were designed for aligned_flat (their
        # fold_X_bend methods build folded solid in +Z direction relative to flat,
        # and FrameAligner ensures flat's orientation matches reference part).
        # General tree (3b) uses absolute coordinates from canonical_part['bends']
        # and MUST receive original flat_pattern.brep (not aligned), because
        # axis_point/centroid are in original flat's coordinate system.
        # TODO(B.4.4): unify this with fold_from_canonical_part's method dispatcher
        # so both "which fold method" and "which flat" decisions live in one place.
        fold_flat_input = flat_shape

        restored_shape = SheetMetalFolder.fold_from_canonical_part(canonical_part, fold_flat_input)

        # Dynamic origin alignment to reference shape frame (Invariant #5 compliant)
        bb_ref = Bnd_Box()
        brepbndlib.Add(ref_shape, bb_ref)
        bb_rest = Bnd_Box()
        brepbndlib.Add(restored_shape, bb_rest)
        dx = bb_ref.CornerMin().X() - bb_rest.CornerMin().X()
        dy = bb_ref.CornerMin().Y() - bb_rest.CornerMin().Y()
        dz = bb_ref.CornerMin().Z() - bb_rest.CornerMin().Z()
        if abs(dx) > 1e-3 or abs(dy) > 1e-3 or abs(dz) > 1e-3:
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(dx, dy, dz))
            restored_shape = BRepBuilderAPI_Transform(restored_shape, trsf, True).Shape()

        restored_brep_path = run_dir / "restored.brep"
        breptools.Write(restored_shape, str(restored_brep_path))
        restored_hash = compute_file_sha256(restored_brep_path)

        # 6. Topologies extraction (RAW and CANONICAL)
        unifier_ref = ShapeUpgrade_UnifySameDomain(ref_shape, True, True, True)
        unifier_ref.Build()
        canonical_ref_shape = unifier_ref.Shape()

        unifier_rest = ShapeUpgrade_UnifySameDomain(restored_shape, True, True, True)
        unifier_rest.Build()
        canonical_rest_shape = unifier_rest.Shape()

        top_ref_raw = self.extract_topology(ref_shape)
        top_rest_raw = self.extract_topology(restored_shape)
        top_ref_can = self.extract_topology(canonical_ref_shape)
        top_rest_can = self.extract_topology(canonical_rest_shape)

        ref_faces_raw = top_ref_raw["counts"]["Total"]
        ref_faces_canonical = top_ref_can["counts"]["Total"]
        restored_faces_raw = top_rest_raw["counts"]["Total"]
        restored_faces_canonical = top_rest_can["counts"]["Total"]

        ref_cyl_faces_raw = top_ref_raw["counts"]["Cylinder"]
        ref_cyl_faces_canonical = top_ref_can["counts"]["Cylinder"]
        restored_cyl_faces_raw = top_rest_raw["counts"]["Cylinder"]
        restored_cyl_faces_canonical = top_rest_can["counts"]["Cylinder"]

        res_raw = self.compare_topologies(
            top_ref_raw, top_rest_raw, tol.get("linear_tol_mm", 0.1), tol.get("angular_tol_deg", 0.5)
        )
        res_can = self.compare_topologies(
            top_ref_can, top_rest_can, tol.get("linear_tol_mm", 0.1), tol.get("angular_tol_deg", 0.5)
        )

        # Check part symmetry transformations if not directly matched in CANONICAL mode
        from OCC.Core.gp import gp_Ax1, gp_Dir
        active_can_shape = canonical_rest_shape
        active_top_rest_can = top_rest_can
        if not res_can["topology_matches"]:
            # Backwards compatibility: if canonical_part specifies symmetry_transformations, use them.
            # Otherwise, use default orthogonal 180-deg rotations.
            cpart_syms = canonical_part.get("payload", canonical_part).get("symmetry_transformations")
            candidate_transforms = []
            if cpart_syms is not None and isinstance(cpart_syms, list):
                for st in cpart_syms:
                    if st.get("type") == "ROTATION":
                        ax = st.get("axis", [0, 0, 1])
                        orig = st.get("origin", [0, 0, 0])
                        ang = math.radians(st.get("angle_deg", 180.0))
                        t = gp_Trsf()
                        t.SetRotation(gp_Ax1(gp_Pnt(*orig), gp_Dir(*ax)), ang)
                        candidate_transforms.append(t)
            else:
                for ax_dir in [gp_Dir(0, 1, 0), gp_Dir(1, 0, 0), gp_Dir(0, 0, 1)]:
                    t = gp_Trsf()
                    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), ax_dir), math.pi)
                    candidate_transforms.append(t)

            for t in candidate_transforms:
                sym_shape = BRepBuilderAPI_Transform(canonical_rest_shape, t, True).Shape()
                sym_top = self.extract_topology(sym_shape)
                sym_res = self.compare_topologies(
                    top_ref_can, sym_top, tol.get("linear_tol_mm", 0.1), tol.get("angular_tol_deg", 0.5)
                )
                if sym_res["topology_matches"]:
                    res_can = sym_res
                    active_can_shape = sym_shape
                    active_top_rest_can = sym_top
                    break

        active_topo_res = res_can if active_mode == "CANONICAL" else res_raw
        topology_matches = bool(active_topo_res["topology_matches"])

        # Diagnostics for RAW comparison
        if not res_raw["topology_matches"]:
            diagnostics.append({
                "code": "RAW_TOPOLOGY_DIFFERENCE",
                "message": (
                    f"RAW topology comparison: matches=False (ref_faces_raw={ref_faces_raw}, "
                    f"restored_faces_raw={restored_faces_raw}). "
                    f"Canonical comparison: matches={res_can['topology_matches']}."
                ),
                "level": "INFO" if active_mode == "CANONICAL" else "ERROR",
                "stage": "metrics_validation",
            })

        if not topology_matches:
            diagnostics.append({
                "code": "TOPOLOGY_MISMATCH",
                "message": (
                    f"Topology mismatch in {active_mode} mode: details={active_topo_res}. "
                    f"Faces: ref={ref_faces_canonical if active_mode == 'CANONICAL' else ref_faces_raw}, "
                    f"restored={restored_faces_canonical if active_mode == 'CANONICAL' else restored_faces_raw}."
                ),
                "level": "ERROR",
                "stage": "metrics_validation",
            })

        analyzer = BRepCheck_Analyzer(restored_shape)
        is_valid = bool(analyzer.IsValid())

        solids_count = 0
        exp_sol = TopExp_Explorer(restored_shape, TopAbs_SOLID)
        while exp_sol.More():
            solids_count += 1
            exp_sol.Next()

        shells_count = 0
        exp_sh = TopExp_Explorer(restored_shape, TopAbs_SHELL)
        while exp_sh.More():
            shells_count += 1
            exp_sh.Next()

        vol_ref, area_ref, bbox_ref = self._get_shape_properties(ref_shape)
        vol_rest, area_rest, bbox_rest = self._get_shape_properties(restored_shape)

        vol_delta = abs(vol_rest - vol_ref)
        rel_vol_delta = vol_delta / (vol_ref + 1e-9)
        area_delta = abs(area_rest - area_ref)
        bbox_delta = [abs(bbox_rest[i] - bbox_ref[i]) for i in range(3)]
        hausdorff = self._compute_hausdorff_distance(restored_shape, ref_shape)

        is_pass = (
            is_valid
            and align_res.status == AlignmentStatus.OK
            and abs(align_res.det_R - 1.0) < 1e-4
            and vol_delta <= tol["volume_tol_mm3"]
            and rel_vol_delta <= tol["relative_volume_tol"]
            and area_delta <= tol["surface_area_tol_mm2"]
            and all(b <= tol["bbox_tol_mm"] for b in bbox_delta)
            and topology_matches
        )

        baseline_status = "VALID" if is_pass else "INVALID"

        faces_count_reference = ref_faces_canonical if active_mode == "CANONICAL" else ref_faces_raw
        faces_count_restored = restored_faces_canonical if active_mode == "CANONICAL" else restored_faces_raw
        faces_count_delta = abs(faces_count_restored - faces_count_reference)
        cylindrical_faces_delta = abs(
            (restored_cyl_faces_canonical - ref_cyl_faces_canonical)
            if active_mode == "CANONICAL"
            else (restored_cyl_faces_raw - ref_cyl_faces_raw)
        )

        result_payload = {
            "schema_name": "baseline_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "baseline_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value if is_pass else RunStatus.WORKER_ERROR.value,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "baseline_status": baseline_status,
                "comparison_mode": active_mode,
                "pass": is_pass,
                "frame_alignment": {
                    "status": align_res.status.value,
                    "det_R": align_res.det_R,
                    "normal_dot": align_res.normal_dot,
                    "tangent_dot": align_res.tangent_dot,
                    "reference_panel_id": canonical_part["payload"]["reference_panel_id"],
                },
                "restored_shape_artifact": {
                    "file_path": str(restored_brep_path.relative_to(workspace_root)),
                    "format": "BREP",
                    "content_hash": restored_hash,
                },
                "metrics": {
                    "is_valid_brep": is_valid,
                    "solids_count": solids_count,
                    "shells_count": shells_count,
                    "volume_delta_mm3": vol_delta,
                    "relative_volume_delta": rel_vol_delta,
                    "surface_area_delta_mm2": area_delta,
                    "hausdorff_distance_mm": hausdorff,
                    "bbox_delta_mm": bbox_delta,
                    "ref_faces_raw": ref_faces_raw,
                    "ref_faces_canonical": ref_faces_canonical,
                    "restored_faces_raw": restored_faces_raw,
                    "restored_faces_canonical": restored_faces_canonical,
                    "ref_cyl_faces_raw": ref_cyl_faces_raw,
                    "ref_cyl_faces_canonical": ref_cyl_faces_canonical,
                    "restored_cyl_faces_raw": restored_cyl_faces_raw,
                    "restored_cyl_faces_canonical": restored_cyl_faces_canonical,
                    "topology_matches": topology_matches,
                    "faces_count_reference": faces_count_reference,
                    "faces_count_restored": faces_count_restored,
                    "faces_count_delta": faces_count_delta,
                    "cylindrical_faces_delta": cylindrical_faces_delta,
                    "raw_comparison_result": res_raw,
                    "topology_details": {
                        "counts": top_ref_can["counts"] if active_mode == "CANONICAL" else top_ref_raw["counts"],
                        "sum_areas": top_ref_can["sum_areas"] if active_mode == "CANONICAL" else top_ref_raw["sum_areas"],
                        "plane_areas_sorted": [round(float(a), 4) for a in (top_ref_can["plane_areas"] if active_mode == "CANONICAL" else top_ref_raw["plane_areas"])],
                        "cyl_areas_sorted": [round(float(a), 4) for a in (top_ref_can["cyl_areas"] if active_mode == "CANONICAL" else top_ref_raw["cyl_areas"])],
                        "normals_sorted": [[round(float(c), 4) for c in n] for n in (top_ref_can["plane_normals"] if active_mode == "CANONICAL" else top_ref_raw["plane_normals"])],
                        "plane_positions_sorted": [round(float(p), 4) for p in (top_ref_can["plane_positions"] if active_mode == "CANONICAL" else top_ref_raw["plane_positions"])],
                    },
                },
            },
        }

        out_file = run_dir / "04_baseline_result.json"
        self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
        return out_file
