"""
sheetmetal_client.py - FreeCAD SheetMetal Workbench API Integration Client

Supports dual unfolding modes:
1. Document GUI / Unattended mode (`SMUnfoldUnattended` via FreeCADGui / xvfb).
2. Direct Headless Python engine mode (`SheetMetalNewUnfolder` via pure Part.Shape geometry).
"""

import os
import sys
import logging
from typing import Optional, Tuple, Any

import FreeCAD

logger = logging.getLogger(__name__)

# Ensure FreeCAD Mod/sheetmetal is in sys.path if present
SHEETMETAL_MOD_PATHS = [
    os.path.expanduser("~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal"),
    os.path.expanduser("~/.local/share/FreeCAD/Mod/sheetmetal"),
    "/usr/share/freecad/Mod/sheetmetal",
]
for mod_path in SHEETMETAL_MOD_PATHS:
    if os.path.exists(mod_path) and mod_path not in sys.path:
        sys.path.append(mod_path)


class SheetMetalApiClientError(Exception):
    """Raised when SheetMetal unfolding or initialization fails."""
    pass


_DEFAULT_K_FACTOR = 0.44
_DEFAULT_STANDARD = "ansi"
_SM_PREF_PATH = "User parameter:BaseApp/Preferences/Mod/SheetMetal"


class SheetMetalApiClient:
    """
    Client wrapper for FreeCAD SheetMetal Workbench unbending algorithms.
    K-factor and standard are read dynamically from SheetMetal preferences on each call.
    """

    def __init__(self):
        """
        K-factor is read from SheetMetal preferences on each call.
        No k_factor parameter — single source of truth is SheetMetal.
        """
        pass

    def _read_k_factor_preferences(self) -> Tuple[float, str]:
        """
        Returns (k_factor, standard).
        Falls back to (_DEFAULT_K_FACTOR, _DEFAULT_STANDARD) if preferences are unavailable or invalid.
        """
        try:
            import FreeCAD
            params = FreeCAD.ParamGet(_SM_PREF_PATH)
            k = params.GetFloat(
                "manualKFactor",
                params.GetFloat("defaultKFactor", _DEFAULT_K_FACTOR),
            )
            std = params.GetString(
                "kFactorStandard",
                params.GetString("defaultKFactorStandard", _DEFAULT_STANDARD),
            )
            if not (0.0 < k <= 1.0):
                logger.warning(
                    "SheetMetal manualKFactor=%s out of range, using %s",
                    k, _DEFAULT_K_FACTOR
                )
                return _DEFAULT_K_FACTOR, _DEFAULT_STANDARD
            return k, (std or _DEFAULT_STANDARD).lower()
        except Exception as e:
            logger.warning("SheetMetal preferences unavailable, using default: %s", e)
            return _DEFAULT_K_FACTOR, _DEFAULT_STANDARD

    def unfold_part(self, doc_name: str, part_name: str, face_name: str) -> Any:
        """
        Unfolds a part in a FreeCAD document using the `SMUnfoldUnattended` GUI command.
        Suitable for GUI mode or headless execution via virtual display (xvfb).

        :param doc_name: Name of active FreeCAD document.
        :param part_name: Name of target part object.
        :param face_name: Name of base flat face (e.g. "Face1").
        :return: Created unfold DocumentObject.
        """
        try:
            import FreeCAD
            import FreeCADGui
        except ImportError as e:
            raise SheetMetalApiClientError(
                "FreeCAD / FreeCADGui modules unavailable. Ensure running inside FreeCAD environment."
            ) from e

        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            raise SheetMetalApiClientError(f"FreeCAD document '{doc_name}' not found.")

        target_obj = doc.getObject(part_name)
        if not target_obj:
            raise SheetMetalApiClientError(f"Object '{part_name}' not found in document '{doc_name}'.")

        # Check if FreeCADGui is fully initialized (has Selection and runCommand)
        if not hasattr(FreeCADGui, 'Selection') or not hasattr(FreeCADGui, 'runCommand'):
            raise SheetMetalApiClientError(
                "FreeCADGui is not initialized with active selection GUI loop. "
                "For headless operation without GUI/xvfb, use client.unfold_shape_headless(shape)."
            )

        # 1. Sync global manual K-factor parameter
        k, std = self._read_k_factor_preferences()
        try:
            param_group = FreeCAD.ParamGet(_SM_PREF_PATH)
            param_group.SetFloat("manualKFactor", k)
            param_group.SetString("kFactorStandard", std)
        except Exception as e:
            logger.warning(f"Could not set manualKFactor parameter: {e}")


        # 2. Select target face for unfolding
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(doc_name, part_name, face_name)

        # 3. Execute SMUnfoldUnattended
        try:
            FreeCADGui.runCommand("SMUnfoldUnattended")
        except Exception as e:
            raise SheetMetalApiClientError(
                f"Failed to execute Gui.runCommand('SMUnfoldUnattended'): {e}"
            ) from e

        doc.recompute()

        # 4. Locate created unfold object
        unfold_obj = None
        for obj in doc.Objects:
            if obj.Name.startswith("Unfold") or "Unfold" in obj.Label:
                unfold_obj = obj
                break

        if not unfold_obj:
            raise SheetMetalApiClientError("Unfold result object not found in FreeCAD document after execution.")

        return unfold_obj

    def unfold_shape_headless(self, fc_shape: Any, root_face_idx: Optional[int] = None) -> Any:
        """
        Direct Python engine unfolding without requiring FreeCADGui or SMUnfoldUnattended.
        Runs natively in headless background tasks.

        :param fc_shape: FreeCAD Part.Shape or OpenCASCADE shape.
        :param root_face_idx: Index of base flat face. If None, automatically selects largest planar face.
        :return: Unfolded Part.Compound / Shape result.
        """
        try:
            import SheetMetalNewUnfolder
        except ImportError as e:
            raise SheetMetalApiClientError(
                "SheetMetalNewUnfolder module not found. Ensure SheetMetal workbench addon is installed."
            ) from e

    def _ensure_fc_shape(self, shape: Any) -> Any:
        """Helper to ensure input is a FreeCAD Part.Shape object."""
        if hasattr(shape, 'Faces'):
            return shape
        import Part
        import tempfile
        from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
        with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tmp:
            step_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(step_path)
        fc_shape = Part.read(step_path)
        os.remove(step_path)
        return fc_shape

    def find_primary_planar_face(self, shape: Any) -> int:
        """
        Selects root face for unfolding & thickness estimation. Criteria (in order):
        1. Must be planar (GeomPlane).
        2. Among planar, largest area.
        3. Tiebreaker: largest distance from bend cylinder axis (outermost flange preferred).
        4. Tiebreaker: lowest face index (deterministic).
        """
        fc_shape = self._ensure_fc_shape(shape)
        cyl_centers = []
        for face in fc_shape.Faces:
            if getattr(face.Surface, 'TypeId', '') == 'Part::GeomCylinder':
                cyl_centers.append(face.Surface.Center)

        planar_faces = []
        for idx, face in enumerate(fc_shape.Faces):
            if face.ShapeType == 'Face' and getattr(face.Surface, 'TypeId', '') == 'Part::GeomPlane':
                c = face.CenterOfMass
                dist_to_cyl = min([c.distanceToPoint(cc) for cc in cyl_centers]) if cyl_centers else 0.0
                planar_faces.append((face.Area, dist_to_cyl, idx))

        if not planar_faces:
            return 0  # Fallback to index 0 if no planar faces exist

        # Primary sort: (-Area rounded to 2 places, -dist_to_cyl rounded to 4 places, idx ascending)
        planar_faces.sort(key=lambda item: (-round(item[0], 2), -round(item[1], 4), item[2]))
        return planar_faces[0][2]

    def unfold_shape_headless(
        self,
        fc_shape: Any,
        root_face_idx: Optional[int] = None,
        standard: Optional[str] = None,
        k_factor_override: Optional[float] = None
    ) -> Any:
        """
        Direct Python engine unfolding without requiring FreeCADGui or SMUnfoldUnattended.
        Runs natively in headless background tasks.

        :param fc_shape: FreeCAD Part.Shape or OpenCASCADE shape.
        :param root_face_idx: Index of base flat face. If None, automatically selects primary planar face.
        :param standard: Optional standard override ('ansi' or 'din').
        :param k_factor_override: Optional K-factor override for tests.
        :return: Unfolded Part.Compound / Shape result.
        """
        try:
            import SheetMetalNewUnfolder
        except ImportError as e:
            raise SheetMetalApiClientError(
                "SheetMetalNewUnfolder module not found. Ensure SheetMetal workbench addon is installed."
            ) from e

        import Part

        # Ensure Part.Shape wrapper
        fc_shape = self._ensure_fc_shape(fc_shape)

        # Auto-detect root face if not provided
        if root_face_idx is None:
            root_face_idx = self.find_primary_planar_face(fc_shape)

        # Build BendAllowanceCalculator from preferences (or overrides)
        k, std = self._read_k_factor_preferences()
        if k_factor_override is not None:
            k = k_factor_override
        if standard is not None:
            std = standard.lower()
        bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(k, std)

        # Estimate exact sheet thickness using SheetMetal's native geometry estimator
        try:
            thickness = SheetMetalNewUnfolder.EstimateThickness.using_best_method(fc_shape, root_face_idx)
        except Exception:
            thickness = 1.5

        # Execute unfolding graph solver
        try:
            doc = FreeCAD.newDocument("UnfoldTempDoc")
            FreeCAD.setActiveDocument(doc.Name)
            part_obj = doc.addObject("Part::Feature", "UnfoldPart")
            part_obj.Shape = fc_shape
            doc.recompute()

            facename = f"Face{root_face_idx + 1}"
            root_face, inplace_unbend, trimmed_bend_lines, root_normal, bend_lines_info = SheetMetalNewUnfolder.getUnfold(
                bac, part_obj, facename
            )
            FreeCAD.closeDocument(doc.Name)
            return inplace_unbend
        except Exception as e:
            logger.warning(f"getUnfold fallback to shapes_list compound: {e}")

        # Fallback to direct shapes_list
        shapes_list, sketch_list = SheetMetalNewUnfolder.unfold(fc_shape, root_face_idx, bac)
        if not shapes_list:
            raise SheetMetalApiClientError("SheetMetalNewUnfolder returned empty unfolded shapes list.")
        return Part.Compound(shapes_list)

    def get_thickness(self, shape: Any, face_idx: Optional[int] = None) -> float:
        """
        Estimates true sheet metal thickness using SheetMetal's native EstimateThickness.
        If face_idx is None, automatically selects the primary (largest planar) face.
        Validates output value against shape bounding box minimum dimension.
        """
        import SheetMetalNewUnfolder
        fc_shape = self._ensure_fc_shape(shape)
        idx = face_idx if face_idx is not None else self.find_primary_planar_face(fc_shape)
        t = SheetMetalNewUnfolder.EstimateThickness.using_best_method(fc_shape, idx)

        # Sanity check: thickness must not exceed smallest bounding box dimension or be <= 0
        bb = fc_shape.BoundBox
        min_dim = min(bb.XLength, bb.YLength, bb.ZLength)
        if not (0.01 < t < min_dim + 1e-6):
            raise SheetMetalApiClientError(
                f"EstimateThickness returned implausible value {t:.4f} for face {idx} (min bbox dim is {min_dim:.4f})"
            )
        return t

    def get_k_factor(
        self,
        radius: float,
        thickness: float,
        standard: Optional[str] = None,
        k_factor_override: Optional[float] = None
    ) -> float:
        """
        Retrieves neutral axis K-factor calculated by SheetMetal for given R, t, and standard.
        """
        import SheetMetalNewUnfolder
        k, std = self._read_k_factor_preferences()
        if k_factor_override is not None:
            k = k_factor_override
        if standard is not None:
            std = standard.lower()
        bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(k, std)
        return bac.get_k_factor(radius, thickness)

    def get_bend_allowance(
        self,
        radius: float,
        thickness: float,
        angle_deg: float,
        standard: Optional[str] = None,
        is_bend_down: bool = False,
        k_factor_override: Optional[float] = None
    ) -> float:
        """
        Calculates exact Bend Allowance (BA) along neutral axis using SheetMetal's BendAllowanceCalculator.
        """
        import SheetMetalNewUnfolder
        import math
        k, std = self._read_k_factor_preferences()
        if k_factor_override is not None:
            k = k_factor_override
        if standard is not None:
            std = standard.lower()
        bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(k, std)
        direction = (SheetMetalNewUnfolder.BendDirection.DOWN if is_bend_down
                     else SheetMetalNewUnfolder.BendDirection.UP)
        angle_rad = math.radians(abs(angle_deg))
        return bac.get_bend_allowance(direction, radius, thickness, angle_rad)


    def get_bend_angle_and_direction(self, cylinder_face: Any) -> Tuple[float, bool]:
        """
        Extracts bend angle (in degrees) and direction (is_down) directly from a cylinder face.
        Supports both FreeCAD Part.Face and OCC TopoDS_Face.
        Includes robust fallback measurement via circular edge arc length for seam cylinders.
        """
        import SheetMetalNewUnfolder
        import math

        if hasattr(cylinder_face, 'ParameterRange'):
            umin, umax, vmin, vmax = cylinder_face.ParameterRange
            u_span = abs(umax - umin)
            
            # Robust check for full seam cylinder: if u_span ~ 2*pi, measure arc from circular edges
            if u_span >= 2.0 * math.pi - 1e-3:
                radius = cylinder_face.Surface.Radius
                circ_edges = [e for e in cylinder_face.Edges if getattr(e.Curve, 'TypeId', '') == 'Part::GeomCircle']
                if circ_edges and radius > 1e-6:
                    u_span = circ_edges[0].Length / radius

            bend_angle_deg = math.degrees(u_span)
            direction = SheetMetalNewUnfolder.BendDirection.from_face(cylinder_face)
            is_down = (direction == SheetMetalNewUnfolder.BendDirection.DOWN)
            return bend_angle_deg, is_down
        else:
            # OpenCASCADE TopoDS_Face
            from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
            from OCC.Core.BRepLProp import BRepLProp_SLProps
            from OCC.Core.TopAbs import TopAbs_FORWARD, TopAbs_EDGE
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopoDS import topods
            from OCC.Core.GeomAbs import GeomAbs_Circle

            surf = BRepAdaptor_Surface(cylinder_face, True)
            u1, u2 = surf.FirstUParameter(), surf.LastUParameter()
            u_span = abs(u2 - u1)

            if u_span >= 2.0 * math.pi - 1e-3:
                radius = surf.Cylinder().Radius()
                exp = TopExp_Explorer(cylinder_face, TopAbs_EDGE)
                while exp.More():
                    c = BRepAdaptor_Curve(topods.Edge(exp.Current()))
                    if c.GetType() == GeomAbs_Circle and radius > 1e-6:
                        from OCC.Core.GCPnts import GCPnts_AbscissaPoint
                        l_arc = GCPnts_AbscissaPoint.Length(c)
                        u_span = l_arc / radius
                        break
                    exp.Next()

            bend_angle_deg = math.degrees(u_span)
            u_mid = (u1 + u2) / 2.0
            v_mid = (surf.FirstVParameter() + surf.LastVParameter()) / 2.0
            props = BRepLProp_SLProps(surf, u_mid, v_mid, 2, 1e-6)
            c_min, c_max = props.MinCurvature(), props.MaxCurvature()
            is_fwd = (cylinder_face.Orientation() == TopAbs_FORWARD)
            eps = 1e-5
            if c_min < -eps and abs(c_max) < eps:
                is_down = True if is_fwd else False
            elif c_max > eps and abs(c_min) < eps:
                is_down = False if is_fwd else True
            else:
                is_down = False
            return bend_angle_deg, is_down

