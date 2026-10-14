"""
Geometry Worker for SMBS / BendSeq.
Compliant with ТЗ v1.2 §4.1, §4.4, §4.5 and Principles 1, 2, 7, 10, 16, 23.
Reads 01_input_manifest.json -> writes 02_geometry_result.json + geometry.brep.
"""

from __future__ import annotations
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from OCC.Core.BRepTools import breptools
from OCC.Core.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE

from smbs_core.protocols.file_store import (
    ConfidenceLevel,
    ContractError,
    FileStore,
    ParameterSource,
    RunStatus,
    compute_file_sha256,
)
from smbs_core.contracts.models import DiagnosticEntry
from smbs_core.workers.sheetmetal.adapter import SheetMetalAdapter, SheetMetalUnfoldResult


class GeometryWorker:
    """
    Worker extracting B-Rep geometry and executing native unfolds if in NATIVE_SHEETMETAL mode.
    Outputs immutable B-Rep binary artifacts and 02_geometry_result.json.
    """

    def __init__(self, file_store: Optional[FileStore] = None):
        self.file_store = file_store or FileStore()
        self.sheetmetal_adapter = SheetMetalAdapter()

    def _load_occ_shape(self, file_path: str) -> Any:
        """Loads shape from STEP or BREP file into OpenCASCADE TopoDS_Shape."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source geometry file not found: {path}")

        if path.suffix.lower() in [".step", ".stp"]:
            reader = STEPControl_Reader()
            status = reader.ReadFile(str(path))
            if status != IFSelect_RetDone:
                raise ValueError(f"Failed to read STEP file: {path}")
            reader.TransferRoots()
            return reader.OneShape()
        elif path.suffix.lower() == ".brep":
            from OCC.Core.TopoDS import TopoDS_Shape
            from OCC.Core.BRep import BRep_Builder
            shape = TopoDS_Shape()
            builder = BRep_Builder()
            if not breptools.Read(shape, str(path), builder):
                raise ValueError(f"Failed to read BREP file: {path}")
            return shape
        elif path.suffix.lower() == ".fcstd":
            # Read via FreeCAD Part
            fc_lib = "/Applications/FreeCAD.app/Contents/Resources/lib"
            if os.path.exists(fc_lib) and fc_lib not in sys.path:
                sys.path.append(fc_lib)
            import FreeCAD
            import Part
            doc = FreeCAD.openDocument(str(path))
            try:
                # Find part feature with Shape
                target_shape = None
                for obj in doc.Objects:
                    if hasattr(obj, "Shape") and obj.Shape.Faces:
                        target_shape = obj.Shape
                        break
                if not target_shape:
                    raise ValueError(f"No suitable shape found in FCStd document {path}")
                # Export to temporary step and read into OCC
                tmp_step = path.with_suffix(".temp_trans.step")
                target_shape.exportStep(str(tmp_step))
                reader = STEPControl_Reader()
                reader.ReadFile(str(tmp_step))
                reader.TransferRoots()
                occ_s = reader.OneShape()
                if tmp_step.exists():
                    tmp_step.unlink()
                return occ_s
            finally:
                FreeCAD.closeDocument(doc.Name)
        else:
            raise ValueError(f"Unsupported geometry format: {path.suffix}")

    def execute(self, manifest_path: Union[str, Path]) -> Path:
        """
        Executes Geometry Worker on given 01_input_manifest.json.
        Returns path to generated 02_geometry_result.json.
        """
        manifest = self.file_store.read_json_verified(manifest_path, expected_schema_name="input_manifest")

        run_id = manifest["run_id"]
        part_id = manifest["part_id"]
        input_fingerprint = manifest["input_fingerprint"]
        payload = manifest["payload"]
        source_file = payload["source_file_path"]
        input_mode = payload["input_mode"]
        run_dir = Path(manifest_path).parent

        diagnostics = []

        # 1. Load OCC shape
        try:
            occ_shape = self._load_occ_shape(source_file)
        except Exception as e:
            err_result = {
                "schema_name": "geometry_result",
                "schema_version": "1.1",
                "run_id": run_id,
                "part_id": part_id,
                "input_fingerprint": input_fingerprint,
                "producer": {"name": "geometry_worker", "version": "1.1"},
                "units": "mm",
                "frame_convention": "STANDARD_CARTESIAN_MM",
                "status": RunStatus.WORKER_ERROR.value,
                "diagnostics": [
                    {
                        "code": "LOAD_ERROR",
                        "message": str(e),
                        "level": "ERROR",
                        "stage": "load_shape"
                    }
                ],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "geometry_artifact": {"file_path": "", "format": "BREP", "content_hash": ""},
                    "thickness_mm": {"value": None, "source": ParameterSource.UNKNOWN.value, "confidence": ConfidenceLevel.UNKNOWN.value},
                    "detected_faces_count": 0,
                    "detected_bends_count": 0,
                    "geometry_status": RunStatus.WORKER_ERROR.value
                }
            }
            out_file = run_dir / "02_geometry_result.json"
            self.file_store.write_json_atomic(out_file, err_result, schema_name="geometry_result")
            return out_file

        # 2. Write immutable geometry.brep artifact
        brep_path = run_dir / "geometry.brep"
        breptools.Write(occ_shape, str(brep_path))
        brep_hash = compute_file_sha256(brep_path)

        # 3. Analyze faces
        faces_count = 0
        exp = TopExp_Explorer(occ_shape, TopAbs_FACE)
        while exp.More():
            faces_count += 1
            exp.Next()

        # 4. If NATIVE_SHEETMETAL, perform native unfold
        flat_pattern_artifact = None
        thickness_val = None
        thickness_source = ParameterSource.GEOMETRIC_INFERENCE
        thickness_confidence = ConfidenceLevel.UNKNOWN
        k_factor_val = None
        bends_count = 0

        if input_mode == "NATIVE_SHEETMETAL":
            if not self.sheetmetal_adapter.is_available:
                status_worker = RunStatus.BACKEND_UNAVAILABLE
                diagnostics.append({
                    "code": "BACKEND_UNAVAILABLE",
                    "message": "Native sheet metal requested but FreeCAD SheetMetal is unavailable.",
                    "level": "ERROR",
                    "stage": "sheetmetal_unfold"
                })
            else:
                import Part
                # Convert occ_shape to Part.Shape for SheetMetal
                tmp_step = run_dir / "temp_for_sm.step"
                writer = STEPControl_Writer()
                writer.Transfer(occ_shape, STEPControl_AsIs)
                writer.Write(str(tmp_step))
                fc_shape = Part.read(str(tmp_step))
                if tmp_step.exists():
                    tmp_step.unlink()

                unfold_res: SheetMetalUnfoldResult = self.sheetmetal_adapter.unfold(
                    fc_shape,
                    k_factor_override=payload.get("k_factor_override"),
                    standard_override=payload.get("standard_override")
                )

                if unfold_res.status == RunStatus.SUCCESS and unfold_res.unfolded_shape:
                    flat_brep_path = run_dir / "flat_pattern.brep"
                    unfold_res.unfolded_shape.exportBrep(str(flat_brep_path))
                    flat_hash = compute_file_sha256(flat_brep_path)
                    flat_bend_lines_data = []
                    for bl in unfold_res.bend_lines:
                        bl_dict = {
                            "bend_id": bl.bend_id,
                            "direction": bl.direction,
                            "angle_deg": bl.angle_deg,
                            "radius_mm": bl.radius_mm,
                            "k_factor": bl.k_factor,
                            "start_point": bl.start_point,
                            "end_point": bl.end_point,
                        }
                        if bl.bend_line_flat is not None:
                            bl_dict["bend_line_flat"] = bl.bend_line_flat
                        flat_bend_lines_data.append(bl_dict)

                    flat_pattern_artifact = {
                        "file_path": str(flat_brep_path.relative_to(run_dir.parent.parent)),
                        "format": "BREP",
                        "content_hash": flat_hash,
                        "bend_lines": flat_bend_lines_data
                    }
                    thickness_val = unfold_res.thickness_mm
                    thickness_source = ParameterSource.SHEETMETAL_NATIVE
                    thickness_confidence = unfold_res.thickness_confidence
                    k_factor_val = unfold_res.k_factor
                    bends_count = len(unfold_res.bend_lines)
                else:
                    diagnostics.extend([
                        {"code": d.code, "message": d.message, "level": d.level, "stage": d.stage or "unfold"}
                        for d in unfold_res.diagnostics
                    ])

        # 5. Assemble and write 02_geometry_result.json
        result_payload = {
            "schema_name": "geometry_result",
            "schema_version": "1.1",
            "run_id": run_id,
            "part_id": part_id,
            "input_fingerprint": input_fingerprint,
            "producer": {"name": "geometry_worker", "version": "1.1"},
            "units": "mm",
            "frame_convention": "STANDARD_CARTESIAN_MM",
            "status": RunStatus.SUCCESS.value,
            "diagnostics": diagnostics,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "geometry_artifact": {
                    "file_path": str(brep_path.relative_to(run_dir.parent.parent)),
                    "format": "BREP",
                    "content_hash": brep_hash
                },
                "flat_pattern_artifact": flat_pattern_artifact,
                "thickness_mm": {
                    "value": thickness_val,
                    "unit": "mm",
                    "source": thickness_source.value,
                    "confidence": thickness_confidence.value
                },
                "k_factor": {
                    "value": k_factor_val,
                    "source": ParameterSource.SHEETMETAL_NATIVE.value if k_factor_val else ParameterSource.UNKNOWN.value,
                    "confidence": ConfidenceLevel.CONFIRMED.value if k_factor_val else ConfidenceLevel.UNKNOWN.value
                },
                "detected_faces_count": faces_count,
                "detected_bends_count": bends_count,
                "root_face_id": "Face1",
                "geometry_status": RunStatus.SUCCESS.value
            }
        }

        out_file = run_dir / "02_geometry_result.json"
        self.file_store.write_json_atomic(out_file, result_payload, schema_name="geometry_result")
        return out_file
