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
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
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
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        counts = {"Plane": 0, "Cylinder": 0, "Other": 0, "Total": 0}
        sum_areas = {"Plane": 0.0, "Cylinder": 0.0, "Other": 0.0, "Total": 0.0}
        plane_areas: List[float] = []
        cyl_areas: List[float] = []
        plane_normals: List[Tuple[float, float, float]] = []

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
                pl = surf.Plane()
                d = pl.Axis().Direction()
                dx, dy, dz = d.X(), d.Y(), d.Z()
                if f.Orientation() == TopAbs_REVERSED:
                    dx, dy, dz = -dx, -dy, -dz
                plane_normals.append(tuple(sorted([abs(dx), abs(dy), abs(dz)])))
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
        plane_normals.sort()

        return {
            "counts": counts,
            "sum_areas": sum_areas,
            "plane_areas": plane_areas,
            "cyl_areas": cyl_areas,
            "plane_normals": plane_normals,
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

        comp_tol = math.sin(math.radians(angular_tol_deg))
        normals_match = len(top_ref["plane_normals"]) == len(top_rest["plane_normals"])
        if normals_match:
            for n_r, n_s in zip(top_ref["plane_normals"], top_rest["plane_normals"]):
                for c_r, c_s in zip(n_r, n_s):
                    if abs(c_r - c_s) > comp_tol:
                        normals_match = False
                        break
                if not normals_match:
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
        canonical_part = self.file_store.read_json_verified(
            canonical_part_path, expected_schema_name="canonical_part"
        )

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

        tol = tolerance_profile or {
            "linear_tol_mm": 0.1,
            "angular_tol_deg": 0.5,
            "volume_tol_mm3": 5.0,
            "relative_volume_tol": 0.01,
            "surface_distance_tol_mm": 0.2,
            "bbox_tol_mm": 0.2,
        }

        diagnostics: List[Dict[str, Any]] = []

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
                    "restored_shape_artifact": {"file_path": "", "format": "BREP", "content_hash": ""},
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

        restored_shape = SheetMetalFolder.fold_from_canonical_part(canonical_part, aligned_flat)

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
        hausdorff = max(bbox_delta) if bbox_delta else 0.0

        is_pass = (
            is_valid
            and align_res.status == AlignmentStatus.OK
            and abs(align_res.det_R - 1.0) < 1e-4
            and vol_delta <= tol["volume_tol_mm3"]
            and rel_vol_delta <= tol["relative_volume_tol"]
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
                },
            },
        }

        out_file = run_dir / "04_baseline_result.json"
        self.file_store.write_json_atomic(out_file, result_payload, schema_name="baseline_result")
        return out_file
