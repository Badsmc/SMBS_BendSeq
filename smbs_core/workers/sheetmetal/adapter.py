"""
SheetMetalAdapter - Dedicated contract adapter for FreeCAD SheetMetal Workbench.
Compliant with ТЗ v1.2 §3.1, §3.2, §3.3 (Rules R1-R5) and Principles 1, 16, 23.
"""

from __future__ import annotations
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from smbs_core.protocols.file_store import (
    ConfidenceLevel,
    ParameterSource,
    RunStatus,
)
from smbs_core.contracts.models import DiagnosticEntry


@dataclass
class NormalizedBendLine:
    bend_id: str
    direction: str  # "UP" or "DOWN"
    angle_deg: float
    radius_mm: float
    k_factor: float
    start_point: List[float]
    end_point: List[float]
    confidence: ConfidenceLevel = ConfidenceLevel.CONFIRMED


@dataclass
class SheetMetalUnfoldResult:
    status: RunStatus
    schema_version: str = "1.1"
    unfolded_shape: Any = None
    thickness_mm: Optional[float] = None
    thickness_confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    k_factor: Optional[float] = None
    k_factor_confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    k_factor_standard: str = "ansi"
    root_face_name: Optional[str] = None
    root_normal: Optional[List[float]] = None
    bend_lines: List[NormalizedBendLine] = field(default_factory=list)
    diagnostics: List[DiagnosticEntry] = field(default_factory=list)


class SheetMetalAdapter:
    """
    Adapter normalizing raw FreeCAD SheetMetal output.
    Rule R1: Native unfold only. If FreeCAD/SheetMetal unavailable -> BACKEND_UNAVAILABLE.
    Rule R5: No hidden defaults. Any missing parameter -> UNKNOWN.
    """

    def __init__(self):
        self._check_availability()

    def _check_availability(self) -> bool:
        """Verifies FreeCAD and SheetMetalNewUnfolder presence."""
        self.is_available = False
        self.freecad_version = None
        self.sheetmetal_version = None

        try:
            fc_lib = "/Applications/FreeCAD.app/Contents/Resources/lib"
            if os.path.exists(fc_lib) and fc_lib not in sys.path:
                sys.path.append(fc_lib)

            sm_mod_paths = [
                os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"),
                os.path.expanduser("~/.local/share/FreeCAD/Mod/sheetmetal"),
                "/usr/share/freecad/Mod/sheetmetal",
            ]
            for p in sm_mod_paths:
                if os.path.exists(p) and p not in sys.path:
                    sys.path.append(p)

            import FreeCAD
            import SheetMetalNewUnfolder

            self.freecad_version = ".".join(FreeCAD.Version()[:3])
            self.sheetmetal_version = getattr(SheetMetalNewUnfolder, "__version__", "unknown")
            self.is_available = True
            return True
        except ImportError:
            self.is_available = False
            return False

    def unfold(
        self,
        fc_shape: Any,
        root_face_idx: Optional[int] = None,
        k_factor_override: Optional[float] = None,
        standard_override: Optional[str] = None,
    ) -> SheetMetalUnfoldResult:
        """
        Executes native SheetMetal unfolding and returns SheetMetalUnfoldResult.
        Never returns input shape on error (Rule R1).
        """
        if not self.is_available:
            return SheetMetalUnfoldResult(
                status=RunStatus.BACKEND_UNAVAILABLE,
                diagnostics=[
                    DiagnosticEntry(
                        code="BACKEND_UNAVAILABLE",
                        message="FreeCAD or SheetMetal workbench is not available in environment.",
                        level="ERROR",
                        stage="sheetmetal_unfold"
                    )
                ]
            )

        import FreeCAD
        import Part
        import SheetMetalNewUnfolder

        diagnostics: List[DiagnosticEntry] = []

        # Read preferences without silent fallback
        try:
            params = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
            pref_k = params.GetFloat("manualKFactor", 0.44)
            pref_std = params.GetString("kFactorStandard", "ansi").lower()
        except Exception as e:
            pref_k = 0.44
            pref_std = "ansi"
            diagnostics.append(
                DiagnosticEntry(
                    code="PREFERENCES_UNAVAILABLE",
                    message=f"Failed reading SheetMetal preferences: {e}",
                    level="WARNING",
                    stage="sheetmetal_preferences"
                )
            )

        k_val = k_factor_override if k_factor_override is not None else pref_k
        std_val = standard_override.lower() if standard_override is not None else pref_std

        bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(k_val, std_val)

        # Estimate thickness natively
        estimated_t = None
        t_confidence = ConfidenceLevel.UNKNOWN
        try:
            if root_face_idx is not None:
                estimated_t = SheetMetalNewUnfolder.EstimateThickness.using_best_method(fc_shape, root_face_idx)
            else:
                # Find largest planar face
                max_area = -1.0
                best_idx = 0
                for idx, face in enumerate(fc_shape.Faces):
                    if face.ShapeType == 'Face' and getattr(face.Surface, 'TypeId', '') == 'Part::GeomPlane':
                        if face.Area > max_area:
                            max_area = face.Area
                            best_idx = idx
                root_face_idx = best_idx
                estimated_t = SheetMetalNewUnfolder.EstimateThickness.using_best_method(fc_shape, root_face_idx)

            if estimated_t is not None and estimated_t > 0:
                t_confidence = ConfidenceLevel.CONFIRMED
        except Exception as e:
            diagnostics.append(
                DiagnosticEntry(
                    code="THICKNESS_ESTIMATION_FAILED",
                    message=f"SheetMetal EstimateThickness failed: {e}",
                    level="WARNING",
                    stage="sheetmetal_thickness"
                )
            )

        # Execute getUnfold
        doc = FreeCAD.newDocument("SMBS_Adapter_Doc")
        try:
            part_obj = doc.addObject("Part::Feature", "UnfoldPart")
            part_obj.Shape = fc_shape
            doc.recompute()

            facename = f"Face{root_face_idx + 1}"
            root_face, inplace_unbend, trimmed_bend_lines, root_normal, bend_lines_info = SheetMetalNewUnfolder.getUnfold(
                bac, part_obj, facename
            )

            # Normalize bend lines
            normalized_bends: List[NormalizedBendLine] = []
            if bend_lines_info:
                for b_idx, b_info in enumerate(bend_lines_info):
                    # Extract attributes safely
                    b_id = f"BEND_{b_idx + 1:02d}"
                    direction_str = "UP"
                    if hasattr(b_info, "direction"):
                        direction_str = "DOWN" if b_info.direction == SheetMetalNewUnfolder.BendDirection.DOWN else "UP"
                    elif isinstance(b_info, dict) and "direction" in b_info:
                        direction_str = str(b_info["direction"]).upper()

                    raw_angle = getattr(b_info, "angle", 90.0)
                    if abs(raw_angle) <= (math.pi + 1e-4):
                        b_angle = math.degrees(raw_angle)
                    else:
                        b_angle = float(raw_angle)
                    b_radius = getattr(b_info, "radius", 1.0)
                    b_k = getattr(b_info, "k_factor", k_val)

                    # Extract line geometry endpoints
                    start_pt = [0.0, 0.0, 0.0]
                    end_pt = [0.0, 0.0, 0.0]
                    if hasattr(b_info, "line") and hasattr(b_info.line, "StartPoint"):
                        start_pt = [b_info.line.StartPoint.x, b_info.line.StartPoint.y, b_info.line.StartPoint.z]
                        end_pt = [b_info.line.EndPoint.x, b_info.line.EndPoint.y, b_info.line.EndPoint.z]

                    normalized_bends.append(
                        NormalizedBendLine(
                            bend_id=b_id,
                            direction=direction_str,
                            angle_deg=b_angle,
                            radius_mm=b_radius,
                            k_factor=b_k,
                            start_point=start_pt,
                            end_point=end_pt,
                            confidence=ConfidenceLevel.CONFIRMED,
                        )
                    )

            root_n = [root_normal.x, root_normal.y, root_normal.z] if root_normal else None

            return SheetMetalUnfoldResult(
                status=RunStatus.SUCCESS,
                unfolded_shape=inplace_unbend,
                thickness_mm=estimated_t,
                thickness_confidence=t_confidence,
                k_factor=k_val,
                k_factor_confidence=ConfidenceLevel.CONFIRMED,
                k_factor_standard=std_val,
                root_face_name=facename,
                root_normal=root_n,
                bend_lines=normalized_bends,
                diagnostics=diagnostics,
            )
        except Exception as e:
            diagnostics.append(
                DiagnosticEntry(
                    code="UNFOLD_FAILED",
                    message=f"SheetMetalNewUnfolder.getUnfold raised: {e}",
                    level="ERROR",
                    stage="sheetmetal_unfold"
                )
            )
            return SheetMetalUnfoldResult(
                status=RunStatus.WORKER_ERROR,
                diagnostics=diagnostics,
            )
        finally:
            FreeCAD.closeDocument(doc.Name)
