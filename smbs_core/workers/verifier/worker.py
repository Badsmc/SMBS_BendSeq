"""
Independent Verifier Worker for SMBS / BendSeq (ТЗ v1.2 §7.1, Principles 8, 24).
Simulates candidate sequence step-by-step using PhysicalValidator without solver cache.
Validates each transition and checks full B-Rep invariants against reference 3D model.
Writes 09_verification_result.json.
"""

from __future__ import annotations
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from OCC.Core.BRepTools import breptools
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_VERTEX, TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, topods
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator, CollisionStatus
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
from smbs_core.Geometry.frame_aligner import FrameAligner, AlignmentStatus
from smbs_core.protocols.file_store import (
    ContractError,
    FileStore,
    RunStatus,
    VerificationStatus,
)


class VerifierWorker:
    """
    Independent Verifier Worker.
    Rule: Never reads solver cache, search tree, or pruned states (§7.1).
    Re-simulates each step in clean execution path (unfold correctness)
    and verifies forward fold reconstruction (fold correctness, KL-11).
    """

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()
        # Clean isolated geometry backend and validator (no shared memo)
        self.backend = PythonOCCBackend()
        self.machine = PressBrakeMachine()
        self.validator = PhysicalValidator(self.machine, self.backend)

    def _load_brep(self, file_path: Union[str, Path]) -> TopoDS_Shape:
        from OCC.Core.BRep import BRep_Builder
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not breptools.Read(shape, str(file_path), builder):
            raise ValueError(f"Failed to load BREP from {file_path}")
        return shape

    def _get_shape_properties(self, shape: TopoDS_Shape) -> Tuple[float, float, List[float]]:
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
    def _find_largest_planar_face(shape: TopoDS_Shape) -> Optional[TopoDS_Face]:
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
        return best_face

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

    def execute(
        self,
        search_result_path: Union[str, Path],
        canonical_part_path: Union[str, Path],
        tolerance_profile: Optional[Dict[str, float]] = None,
        unknown_policy: str = "PRODUCTION"
    ) -> Path:
        """
        Executes independent verification of search result.
        Writes 09_verification_result.json.
        """
        search_result = self.file_store.read_json_verified(search_result_path, expected_schema_name="search_result")
        canonical_part = self.file_store.read_json_verified(canonical_part_path, expected_schema_name="canonical_part")

        run_id = search_result["run_id"]
        part_id = search_result["part_id"]
        input_fingerprint = search_result["input_fingerprint"]
        run_dir = Path(search_result_path).parent
        workspace_root = run_dir.parent.parent

        tol = tolerance_profile or {
            "linear_tol_mm": 0.1,
            "angular_tol_deg": 0.5,
            "volume_tol_mm3": 5.0,
            "relative_volume_tol": 0.01,
            "surface_distance_tol_mm": 0.2,
            "bbox_tol_mm": 0.2
        }

        diagnostics: List[Dict[str, Any]] = []

        # 1. Precheck if solver succeeded
        planning_status = search_result["payload"].get("planning_status")
        candidate_seq = search_result["payload"].get("candidate_sequence", [])

        if planning_status != "SUCCESS" or not candidate_seq:
            verif_payload = {
                "schema_name": "verification_result",
                "schema_version": "1.1",
                "run_id": run_id,
                "part_id": part_id,
                "input_fingerprint": input_fingerprint,
                "producer": {"name": "verifier_worker", "version": "1.1"},
                "units": "mm",
                "frame_convention": "STANDARD_CARTESIAN_MM",
                "status": RunStatus.VERIFICATION_FAILED.value,
                "diagnostics": [
                    {
                        "code": "SOLVER_FAILED",
                        "message": f"Cannot verify sequence because solver returned {planning_status}.",
                        "level": "ERROR",
                        "stage": "verifier_precheck"
                    }
                ],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "verification_status": VerificationStatus.VERIFICATION_FAILED.value,
                    "verification_independence_level": "ISOLATED_OCC_NO_SOLVER_CACHE",
                    "production_eligible": False,
                    "step_verifications": [],
                    "independent_metrics": {
                        "is_valid_brep": False,
                        "solids_count": 0,
                        "shells_count": 0,
                        "volume_delta_mm3": 9999.0,
                        "relative_volume_delta": 1.0,
                        "surface_area_delta_mm2": 9999.0,
                        "hausdorff_distance_mm": 9999.0,
                        "bbox_delta_mm": [9999.0, 9999.0, 9999.0]
                    }
                }
            }
            out_file = run_dir / "09_verification_result.json"
            self.file_store.write_json_atomic(out_file, verif_payload, schema_name="verification_result")
            return out_file

        # 2. Load reference shape and flat pattern shape
        geom_artifact_rel = canonical_part["payload"]["geometry_artifact"]["file_path"]
        ref_brep_path = workspace_root / geom_artifact_rel
        if not ref_brep_path.exists():
            ref_brep_path = run_dir / Path(geom_artifact_rel).name
        ref_shape = self._load_brep(ref_brep_path)

        flat_brep_path = run_dir / "flat_pattern.brep"
        flat_shape = self._load_brep(flat_brep_path) if flat_brep_path.exists() else None

        # 3. Build independent PanelGraph, BendGraph, FoldState
        panels_dict = canonical_part["payload"].get("panels", {})
        bends_dict = canonical_part["payload"].get("bends", {})

        panel_graph = PanelGraph()
        for pid, pdata in panels_dict.items():
            panel_graph.add_panel(Panel(pid, normal=tuple(pdata.get("normal", (0.0, 0.0, 1.0)))))
        for bid, bdata in bends_dict.items():
            panel_graph.add_bend_connection(bid, bdata["parent_panel_id"], bdata["child_panel_id"])

        bend_records: Dict[str, BendRecord] = {}
        bend_graph = BendGraph()
        for bid, bdata in bends_dict.items():
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=bdata["parent_panel_id"],
                child_panel_id=bdata["child_panel_id"],
                axis_origin=tuple(bdata["axis_point"]),
                axis_direction=tuple(bdata["axis_direction"]),
                signed_angle=bdata["angle_deg"]["value"],
                length=bdata["length_mm"]["value"],
                radius=bdata["radius_mm"]["value"],
                thickness=canonical_part["payload"]["thickness_mm"]["value"]
            )
            bend_records[bid] = rec
            bend_graph.add_bend(rec)

        # Tooling setup for verifier
        search_req_file = run_dir / "06_search_request.json"
        machine_config = None
        if search_req_file.exists():
            try:
                s_req = self.file_store.read_json_verified(search_req_file, expected_schema_name="search_request")
                machine_config = s_req["payload"].get("machine_config")
            except Exception:
                pass

        backend = PythonOCCBackend()
        machine = PressBrakeMachine.from_config(machine_config) if machine_config else PressBrakeMachine()
        validator = PhysicalValidator(machine, backend)

        current_state = FoldState.initial(
            part_shape=ref_shape,
            bend_graph=bend_graph,
            panel_graph=panel_graph
        )

        step_verifications: List[Dict[str, Any]] = []
        has_invalid_any = False
        unknown_steps: List[int] = []

        for step_data in candidate_seq:
            st_num = step_data.get("step", len(step_verifications) + 1)
            bid = step_data.get("bend_id")
            bend_rec = bend_graph.get_bend(bid) if bid else None

            if not bend_rec or current_state is None:
                step_verifications.append({
                    "step": st_num,
                    "bend_id": bid or "UNKNOWN",
                    "status": VerificationStatus.VERIFICATION_FAILED.value,
                    "collision_verified": False,
                    "kinematics_verified": False
                })
                has_invalid_any = True
                diagnostics.append({
                    "code": "BEND_NOT_FOUND",
                    "message": f"Bend '{bid}' not found in part bend graph at step {st_num}.",
                    "level": "ERROR",
                    "stage": "step_verification"
                })
                break

            val_res = validator.validate_step(
                current_state=current_state,
                bend_record=bend_rec,
                panel_graph=panel_graph,
                unknown_policy=unknown_policy
            )

            is_valid_step = bool(val_res.valid)
            step_has_unknown = bool(getattr(val_res, "has_unknown", False))
            coll_verified = (val_res.collision_status != CollisionStatus.INVALID)

            step_verifications.append({
                "step": st_num,
                "bend_id": bid,
                "status": VerificationStatus.VERIFIED.value if is_valid_step else VerificationStatus.VERIFICATION_FAILED.value,
                "collision_verified": coll_verified,
                "kinematics_verified": is_valid_step
            })

            if not is_valid_step:
                has_invalid_any = True
                diagnostics.append({
                    "code": "STEP_VALIDATION_FAILED",
                    "message": f"Step {st_num} bend '{bid}' failed: {val_res.reason} (stage={val_res.stage})",
                    "level": "ERROR",
                    "stage": "step_verification"
                })
                break

            if step_has_unknown:
                unknown_steps.append(st_num)

            current_state = val_res.next_state

        # 4. Final shape comparison (unfold correctness invariants)
        # In backward unfolding, final state reaches kinematic flat pattern
        # Target for comparison is flat_shape (or if missing, reference volume/topology invariants)
        final_sim_shape = current_state.shape if current_state and current_state.shape else ref_shape
        target_shape = flat_shape if flat_shape is not None else ref_shape

        analyzer = BRepCheck_Analyzer(final_sim_shape)
        is_valid_brep = bool(analyzer.IsValid())

        solids_count = 0
        exp_sol = TopExp_Explorer(final_sim_shape, TopAbs_SOLID)
        while exp_sol.More():
            solids_count += 1
            exp_sol.Next()

        shells_count = 0
        exp_sh = TopExp_Explorer(final_sim_shape, TopAbs_SHELL)
        while exp_sh.More():
            shells_count += 1
            exp_sh.Next()

        vol_sim, area_sim, bbox_sim = self._get_shape_properties(final_sim_shape)
        vol_target, area_target, bbox_target = self._get_shape_properties(target_shape)

        vol_delta = abs(vol_sim - vol_target)
        rel_vol_delta = vol_delta / vol_target if vol_target > 0 else 1.0
        area_delta = abs(area_sim - area_target)
        bbox_delta = [abs(a - b) for a, b in zip(bbox_sim, bbox_target)]

        # Independent distance metric against target shape
        hausdorff = self._compute_hausdorff_distance(final_sim_shape, target_shape)

        metrics_ok = (
            is_valid_brep
            and vol_delta <= tol["volume_tol_mm3"]
            and rel_vol_delta <= tol["relative_volume_tol"]
            and area_delta <= tol["volume_tol_mm3"]
            and all(d <= tol["bbox_tol_mm"] for d in bbox_delta)
            and hausdorff <= tol["surface_distance_tol_mm"]
        )

        # 5. Fold correctness verification (KL-11 forward reconstruction)
        # Only executed if sequence steps did not encounter invalid transitions
        fold_metrics_ok = True
        if not has_invalid_any:
            if flat_shape is None:
                fold_metrics_ok = False
                diagnostics.append({
                    "code": "FOLD_CHECK_SKIPPED_NO_FLAT_SHAPE",
                    "message": "flat_pattern.brep missing; fold correctness cannot be verified.",
                    "level": "ERROR",
                    "stage": "fold_correctness"
                })
            else:
                try:
                    ref_face = self._find_largest_planar_face(ref_shape)
                    flat_face = self._find_largest_planar_face(flat_shape)
                    if not ref_face or not flat_face:
                        fold_metrics_ok = False
                        diagnostics.append({
                            "code": "ALIGNMENT_FAILED",
                            "message": "Cannot find planar faces for flat pattern alignment.",
                            "level": "ERROR",
                            "stage": "fold_correctness"
                        })
                    else:
                        align_res = FrameAligner.align_full_frame(flat_face, ref_face)
                        if align_res.status != AlignmentStatus.OK:
                            fold_metrics_ok = False
                            diagnostics.append({
                                "code": "ALIGNMENT_FAILED",
                                "message": f"FrameAligner failed: {align_res.error_message}",
                                "level": "ERROR",
                                "stage": "fold_correctness"
                            })
                        else:
                            aligned_flat = BRepBuilderAPI_Transform(flat_shape, align_res.transform, True).Shape()
                            try:
                                folded_sim_shape = SheetMetalFolder.fold_from_canonical_part(
                                    canonical_part, aligned_flat=aligned_flat
                                )
                            except Exception as fold_err:
                                fold_metrics_ok = False
                                diagnostics.append({
                                    "code": "FOLD_RECONSTRUCTION_FAILED",
                                    "message": f"SheetMetalFolder.fold_from_canonical_part failed: {fold_err}",
                                    "level": "ERROR",
                                    "stage": "fold_correctness"
                                })
                                folded_sim_shape = None

                            if folded_sim_shape is not None:
                                vol_sim_folded, area_sim_folded, bbox_sim_folded = self._get_shape_properties(folded_sim_shape)
                                vol_ref_orig, area_ref_orig, bbox_ref_orig = self._get_shape_properties(ref_shape)
                                vol_delta_folded = abs(vol_sim_folded - vol_ref_orig)
                                bbox_delta_folded = [abs(a - b) for a, b in zip(bbox_sim_folded, bbox_ref_orig)]
                                max_bbox_deviation = max(bbox_delta_folded) if bbox_delta_folded else 0.0

                                fold_pass = (
                                    vol_delta_folded <= tol["volume_tol_mm3"]
                                    and all(d <= tol["bbox_tol_mm"] for d in bbox_delta_folded)
                                )
                                if not fold_pass:
                                    fold_metrics_ok = False
                                    diagnostics.append({
                                        "code": "FOLD_SHAPE_MISMATCH",
                                        "message": f"Fold reconstruction does not match ref_shape: vol_delta={vol_delta_folded:.4f}, max_bbox_deviation={max_bbox_deviation:.4f}",
                                        "level": "ERROR",
                                        "stage": "fold_correctness"
                                    })
                except Exception as outer_fold_err:
                    fold_metrics_ok = False
                    diagnostics.append({
                        "code": "FOLD_RECONSTRUCTION_FAILED",
                        "message": f"Unexpected error during fold correctness check: {outer_fold_err}",
                        "level": "ERROR",
                        "stage": "fold_correctness"
                    })

        # 6. Evaluate final verification status
        has_all_bends = (len(candidate_seq) == len(canonical_part["payload"]["bends"]))

        if has_invalid_any or not has_all_bends:
            verif_status = VerificationStatus.VERIFICATION_FAILED
            production_eligible = False
        elif unknown_steps:
            # Under both PRODUCTION and EXPLORATORY, presence of UNKNOWN steps prevents VERIFIED status
            # Schema enum does not support PARTIAL, so VERIFICATION_FAILED with diagnostic is returned
            verif_status = VerificationStatus.VERIFICATION_FAILED
            production_eligible = False
            diag_code = "UNKNOWN_STEPS_UNDER_EXPLORATORY" if unknown_policy == "EXPLORATORY" else "UNKNOWN_STEPS_UNDER_PRODUCTION"
            diagnostics.append({
                "code": diag_code,
                "message": f"Sequence contains {len(unknown_steps)} steps with UNKNOWN subchecks at steps {unknown_steps} under {unknown_policy} policy.",
                "level": "ERROR",
                "stage": "verification"
            })
        elif not metrics_ok:
            verif_status = VerificationStatus.VERIFICATION_FAILED
            production_eligible = False
            diagnostics.append({
                "code": "METRIC_TOLERANCE_EXCEEDED",
                "message": f"Shape comparison exceeded tolerances (vol_delta={vol_delta:.4f}, hausdorff={hausdorff:.4f})",
                "level": "ERROR",
                "stage": "shape_metrics"
            })
        elif not fold_metrics_ok:
            verif_status = VerificationStatus.VERIFICATION_FAILED
            production_eligible = False
        else:
            verif_status = VerificationStatus.VERIFIED
            production_eligible = True

        overall_status = RunStatus.SUCCESS.value if verif_status == VerificationStatus.VERIFIED else RunStatus.WORKER_ERROR.value

        verif_payload = {
            "schema_name": "verification_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "verifier_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": overall_status,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "verification_status": verif_status.value,
                "verification_independence_level": "ISOLATED_OCC_NO_SOLVER_CACHE",
                "production_eligible": production_eligible,
                "step_verifications": step_verifications,
                "independent_metrics": {
                    "is_valid_brep": is_valid_brep,
                    "solids_count": solids_count,
                    "shells_count": shells_count,
                    "volume_delta_mm3": vol_delta,
                    "relative_volume_delta": rel_vol_delta,
                    "surface_area_delta_mm2": area_delta,
                    "hausdorff_distance_mm": hausdorff,
                    "bbox_delta_mm": bbox_delta
                }
            }
        }

        out_file = run_dir / "09_verification_result.json"
        self.file_store.write_json_atomic(out_file, verif_payload, schema_name="verification_result")
        return out_file
