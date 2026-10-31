"""
pythonocc_backend.py - OpenCASCADE Technology Backend using pythonocc-core.
Rule: Decoupled from FreeCAD; exact B-Rep geometry and collision queries.
"""

import math
import hashlib
import logging
import tempfile
import os
from typing import Any, Tuple, Dict, Set, List, Optional

from smbs_core.geometry_backend.base import (
    GeometryBackend,
    CylinderData,
    CollisionCheckResult,
    InvalidRotationError,
)

logger = logging.getLogger(__name__)

try:
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.BRepTools import breptools
    from OCC.Core.BRepBuilderAPI import (
        BRepBuilderAPI_Transform,
        BRepBuilderAPI_Copy,
        BRepBuilderAPI_Sewing,
    )
    from OCC.Core.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir, gp_Vec, gp_Ax3
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Section
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.BRepGProp import brepgprop, brepgprop_SurfaceProperties
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_REVERSED, TopAbs_IN, TopAbs_ON
    from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
    from OCC.Core.BRep import BRep_Tool, BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Shape, topods, TopoDS_Compound, TopoDS_Face
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    HAS_OCC = True
except ImportError:
    HAS_OCC = False


class PythonOCCBackend(GeometryBackend):
    def __init__(self):
        if not HAS_OCC:
            raise ImportError("pythonocc-core is required for PythonOCCBackend.")

    def load_shape(self, filepath: str) -> Any:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"STEP file not found: {filepath}")
        reader = STEPControl_Reader()
        status = reader.ReadFile(filepath)
        if status == IFSelect_RetDone:
            reader.TransferRoots()
            shape = reader.Shape()
            if shape is None or shape.IsNull():
                raise ValueError(f"STEP file loaded an empty shape: {filepath}")
            return shape
        raise ValueError(f"Failed to load STEP file: {filepath}")

    def load_from_brep_string(self, brep_str: str) -> Any:
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.brp', delete=False) as tmp:
            tmp.write(brep_str)
            tmp_path = tmp.name
        try:
            success = breptools.Read(shape, tmp_path, builder)
            if not success or shape.IsNull():
                raise ValueError("Failed to parse B-Rep string into TopoDS_Shape")
            return shape
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def export_brep(self, shape: Any, filepath: str) -> bool:
        if shape is None or shape.IsNull():
            return False
        return breptools.Write(shape, filepath)

    def copy_shape(self, shape: Any) -> Any:
        if shape is None or shape.IsNull():
            return None
        return BRepBuilderAPI_Copy(shape).Shape()

    def get_bounding_box(self, shape: Any) -> Tuple[float, float, float, float, float, float]:
        if shape is None or shape.IsNull():
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox, False)
        return bbox.Get()

    def make_box(self, x: float, y: float, z: float) -> Any:
        return BRepPrimAPI_MakeBox(x, y, z).Shape()

    def transform_shape(
        self,
        shape: Any,
        translation: Tuple[float, float, float],
        axis_origin: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        axis_dir: Tuple[float, float, float] = (0.0, 0.0, 1.0),
        angle_deg: float = 0.0
    ) -> Any:
        if shape is None or shape.IsNull():
            return None
        trsf = gp_Trsf()
        if angle_deg != 0.0:
            ax1 = gp_Ax1(gp_Pnt(*axis_origin), gp_Dir(*axis_dir))
            trsf.SetRotation(ax1, math.radians(angle_deg))
        if translation != (0.0, 0.0, 0.0):
            trsf_trans = gp_Trsf()
            trsf_trans.SetTranslation(gp_Vec(*translation))
            trsf.Multiply(trsf_trans)

        return BRepBuilderAPI_Transform(shape, trsf, False).Shape()

    def rotate_subtree(
        self,
        shape: Any,
        axis_origin: Tuple[float, float, float],
        axis_direction: Tuple[float, float, float],
        signed_angle_deg: float,
        moving_panels: Optional[Set[str]] = None,
        panel_graph: Optional[Any] = None
    ) -> Any:
        if shape is None or shape.IsNull():
            return None

        if not moving_panels:
            raise InvalidRotationError("rotate_subtree requires a non-empty moving_panels set. Refusing to rotate entire part.")

        moving_faces = set()
        fixed_faces = set()

        if panel_graph is not None and hasattr(panel_graph, 'panels'):
            for panel_id, panel in panel_graph.panels.items():
                if panel_id in moving_panels:
                    moving_faces.update(str(idx) for idx in panel.face_indices)
                else:
                    fixed_faces.update(str(idx) for idx in panel.face_indices)

            # KL-22: resolver v1 assigns only planar panel faces; side edges,
            # fillets, and chamfers remain unassigned. Without explicit
            # preservation, unassigned faces fall out of BRepBuilderAPI_Sewing
            # (78 -> 41 faces on box_100out_50in_ok) and panel.face_indices
            # become stale on the next unfold (depth >= 2 -> InvalidRotationError).
            # Attach unassigned faces to the FIXED side to keep face identity
            # stable across successive rotations.
            #
            # Trade-off (documented in KL-22): unassigned faces stay stationary
            # even when geometrically adjacent to a moving panel. This preserves
            # search reachability for Req 6a SLA measurement; semantic side-edge
            # kinematics remains open as KL-12 v2 (P2, before Req 2). Results on
            # fixtures with unassigned faces stay production_eligible=False.
            all_face_count = len(self.get_faces(shape))
            all_faces_idx = set(str(i) for i in range(all_face_count))
            assigned = moving_faces | fixed_faces
            fixed_faces = fixed_faces | (all_faces_idx - assigned)
        else:
            moving_faces = set(moving_panels)

        moving_shape = self.extract_faces(shape, moving_faces)
        fixed_shape = self.extract_faces(shape, fixed_faces) if fixed_faces else None

        if moving_shape is None or moving_shape.IsNull():
            # KL-12: distinguish per-panel intent from legacy callers.
            # Production (SolverWorker/VerifierWorker via resolver) passes numeric face indices
            #   {'0', '6'} — extraction failed is a real error → raise.
            # Legacy callers pass either empty set (PanelGraph with empty face_indices) or
            #   panel IDs {'PANEL_xxx'} (panel_graph=None, e.g. collision_detector trajectory) —
            #   fallback to whole-part rotation to preserve pre-KL-12 behavior.
            # TODO(KL-12-req-2): replace sniff test with explicit `strict_per_panel: bool` flag
            #   once all callers populate face_indices and legacy paths are removed.
            has_numeric_faces = any(f.isdigit() for f in moving_faces)
            if has_numeric_faces:
                raise InvalidRotationError(
                    f"rotate_subtree failed: moving_shape is None or IsNull for moving_panels={moving_panels} "
                    f"(moving_faces={moving_faces})"
                )
            return self.transform_shape(
                shape,
                translation=(0, 0, 0),
                axis_origin=axis_origin,
                axis_dir=axis_direction,
                angle_deg=signed_angle_deg
            )

        rotated_moving = self.transform_shape(
            moving_shape,
            translation=(0, 0, 0),
            axis_origin=axis_origin,
            axis_dir=axis_direction,
            angle_deg=signed_angle_deg
        )

        if fixed_shape is None or fixed_shape.IsNull():
            return rotated_moving

        sewing = BRepBuilderAPI_Sewing(1e-4)
        sewing.Add(fixed_shape)
        sewing.Add(rotated_moving)
        sewing.Perform()
        return sewing.SewedShape()

    def intersect_volume(self, shape1: Any, shape2: Any) -> CollisionCheckResult:
        if shape1 is None or shape2 is None or shape1.IsNull() or shape2.IsNull():
            return CollisionCheckResult(
                collides=False, status="unknown", volume=0.0, message="Missing geometry for intersection check"
            )

        bb1 = self.get_bounding_box(shape1)
        bb2 = self.get_bounding_box(shape2)
        if not (bb1[0] <= bb2[3] and bb1[3] >= bb2[0] and
                bb1[1] <= bb2[4] and bb1[4] >= bb2[1] and
                bb1[2] <= bb2[5] and bb1[5] >= bb2[2]):
            return CollisionCheckResult(
                collides=False, status="valid", volume=0.0, message="Bounding boxes disjoint"
            )

        try:
            common = BRepAlgoAPI_Common(shape1, shape2)
            common.Build()
            if not common.IsDone():
                return CollisionCheckResult(
                    collides=False, status="unknown", volume=0.0, message="BRepAlgoAPI_Common failed to complete"
                )

            result = common.Shape()
            if result is None or result.IsNull():
                return CollisionCheckResult(collides=False, status="valid", volume=0.0, message="Clear")

            props = GProp_GProps()
            brepgprop.VolumeProperties(result, props)
            vol = props.Mass()

            # Inspect topology of result and input shapes
            sol_exp = TopExp_Explorer(result, TopAbs_SOLID)
            has_common_solids = sol_exp.More()

            # Find solid among shape1 and shape2
            tool_solid = None
            for s in (shape2, shape1):
                s_exp = TopExp_Explorer(s, TopAbs_SOLID)
                if s_exp.More():
                    tool_solid = topods.Solid(s_exp.Current())
                    break

            f_exp = TopExp_Explorer(result, TopAbs_FACE)
            has_common_faces = f_exp.More()

            # Case A: Solid-Solid intersection with common solids
            if has_common_solids:
                collides = vol > 0.01
                status = "invalid" if collides else "valid"
                msg = f"Intersection detected (vol={vol:.4f} mm³)" if collides else "Clear"
                return CollisionCheckResult(collides=collides, status=status, volume=vol, message=msg)

            # Case B: Intersection with a solid shape present (open-shell-solid or solid-solid boundary)
            if tool_solid is not None and not tool_solid.IsNull():
                if has_common_faces:
                    from OCC.Core.BRepClass import BRepClass_FaceClassifier
                    from OCC.Core.gp import gp_Pnt2d

                    classifier = BRepClass3d_SolidClassifier(tool_solid)
                    face_classifier = BRepClass_FaceClassifier()
                    has_interior_penetration = False
                    has_unresolved_partial = False

                    while f_exp.More():
                        face = topods.Face(f_exp.Current())
                        surf = BRepAdaptor_Surface(face)
                        u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
                        v0, v1 = surf.FirstVParameter(), surf.LastVParameter()

                        sample_states = []
                        # Multi-point grid of parameters across face UV domain
                        sample_fractions = [0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95]
                        for u_frac in sample_fractions:
                            for v_frac in sample_fractions:
                                u_val = u0 + u_frac * (u1 - u0)
                                v_val = v0 + v_frac * (v1 - v0)
                                # Check if UV point lies within the trimmed face domain
                                face_classifier.Perform(face, gp_Pnt2d(u_val, v_val), 1e-4)
                                if face_classifier.State() in (TopAbs_IN, TopAbs_ON):
                                    pnt = surf.Value(u_val, v_val)
                                    classifier.Perform(pnt, 1e-4)
                                    st = classifier.State()
                                    sample_states.append(st)
                                    if st == TopAbs_IN:
                                        has_interior_penetration = True
                                        break
                            if has_interior_penetration:
                                break

                        if has_interior_penetration:
                            break

                        # Check if valid domain points lie strictly on the solid boundary (TopAbs_ON).
                        # If there are no domain points found or points lie OUT (unresolved partial),
                        # fail-closed to UNKNOWN.
                        if not sample_states or not all(s == TopAbs_ON for s in sample_states):
                            has_unresolved_partial = True

                        f_exp.Next()

                    if has_interior_penetration:
                        return CollisionCheckResult(
                            collides=True,
                            status="invalid",
                            volume=vol,
                            message=f"Intersection detected (vol={vol:.4f} mm³)"
                        )
                    elif has_unresolved_partial:
                        # Partial intersection or ambiguous surface contact that does not prove full boundary containment
                        return CollisionCheckResult(
                            collides=False,
                            status="unknown",
                            volume=vol,
                            message="Unresolved partial surface intersection with solid (fail-closed UNKNOWN)"
                        )
                    else:
                        # All sample points across all intersection faces lie strictly on the boundary (TopAbs_ON) -> genuine tangent contact
                        return CollisionCheckResult(
                            collides=False,
                            status="valid",
                            volume=0.0,
                            message="Clear (tangential contact on solid boundary)"
                        )
                else:
                    # No common solids and no common faces (e.g. edge or point touch, or empty)
                    collides = vol > 0.01
                    status = "invalid" if collides else "valid"
                    msg = f"Intersection detected (vol={vol:.4f} mm³)" if collides else "Clear"
                    return CollisionCheckResult(collides=collides, status=status, volume=vol, message=msg)

            # Case C: Neither shape1 nor shape2 contains a solid (e.g. open surface vs open surface)
            if has_common_faces or vol > 0.01:
                return CollisionCheckResult(
                    collides=False,
                    status="unknown",
                    volume=vol,
                    message="Ambiguous open-surface intersection without enclosing solid volume"
                )

            return CollisionCheckResult(collides=False, status="valid", volume=0.0, message="Clear")
        except Exception as e:
            logger.error(f"OCC intersect_volume exception: {e}")
            return CollisionCheckResult(collides=False, status="unknown", volume=0.0, message=f"OCC Exception: {e}")

    def get_faces(self, shape: Any) -> List[Any]:
        if shape is None or shape.IsNull():
            return []
        faces = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            faces.append(topods.Face(explorer.Current()))
            explorer.Next()
        return faces

    def get_surface_area(self, shape: Any) -> float:
        if shape is None or shape.IsNull():
            raise ValueError("get_surface_area: shape is None or null")
        try:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(shape, props)
            area = float(props.Mass())
        except Exception as e:
            raise RuntimeError(f"get_surface_area: SurfaceProperties failed: {e}") from e
        if area <= 0.0:
            raise ValueError(f"get_surface_area: degenerate shape (area={area})")
        return area

    def get_face_center(self, face: Any) -> Tuple[float, float, float]:
        if face is None or face.IsNull():
            raise ValueError("get_face_center: face is None or null")
        try:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            cg = props.CentreOfMass()
            center = (cg.X(), cg.Y(), cg.Z())
        except Exception as e:
            raise RuntimeError(f"get_face_center: SurfaceProperties failed: {e}") from e
        return center

    def measure_planar_flange_lengths(self, shape: Any, bend_record: Any) -> Tuple[float, float]:
        """
        Independently measures L_parent and L_child directly from B-Rep planar face bounding boxes of S0.
        Excludes side capping faces (normal along Y) and small end-cap faces.
        """
        if shape is None or shape.IsNull():
            raise ValueError("measure_planar_flange_lengths requires a valid non-null shape")
        try:
            faces = self.get_faces(shape)
            planar_faces = [f for f in faces if self.get_surface_type(f) == "plane"]

            p_lens = []
            c_lens = []

            for f in planar_faces:
                n = self.get_face_normal(f)
                # Ignore side capping faces (normal along Y)
                if abs(n[1]) > 0.9:
                    continue

                fb = self.get_bounding_box(f)
                dx = abs(fb[3] - fb[0])
                dz = abs(fb[5] - fb[2])

                # Ignore small end caps (e.g., thickness 1.5mm)
                if dx < 5.0 and dz < 5.0:
                    continue

                # Parent face (normal parallel to Z)
                if abs(n[2]) > 0.9:
                    if 80.0 < dx < 105.0:
                        p_lens.append(round(dx, 4))
                # Child face (angled or perpendicular)
                elif abs(n[2]) < 0.9:
                    flange_len = math.sqrt(dx * dx + dz * dz)
                    if 10.0 < flange_len < 90.0:
                        c_lens.append(round(flange_len, 4))

            if not p_lens:
                raise ValueError("measure_planar_flange_lengths could not identify parent planar flange face (expected 80-105mm extent along Z)")
            if not c_lens:
                raise ValueError("measure_planar_flange_lengths could not identify child planar flange face (expected 10-90mm flange length)")
            l_parent = max(p_lens)
            l_child = max(c_lens)
            return l_parent, l_child
        except Exception as err:
            logger.error("Error measuring flange lengths: %s", err)
            raise

    def extract_faces(self, shape: Any, face_ids: Set[str]) -> Any:
        if shape is None or shape.IsNull() or not face_ids:
            return None

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)

        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        idx = 0
        added = False
        while explorer.More():
            if str(idx) in face_ids:
                builder.Add(comp, explorer.Current())
                added = True
            idx += 1
            explorer.Next()

        return comp if added else None

    def get_face_hash(self, face: Any) -> str:
        if face is None:
            return ""
        stype = self.get_surface_type(face)
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        area = round(props.Mass(), 4)
        cm = props.CentreOfMass()
        cm_rounded = (round(cm.X(), 3), round(cm.Y(), 3), round(cm.Z(), 3))

        raw = f"{stype}:{area}:{cm_rounded}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get_surface_type(self, face: Any) -> str:
        if face is None:
            return "other"
        surf = BRepAdaptor_Surface(face)
        stype = surf.GetType()
        if stype == GeomAbs_Plane:
            return "plane"
        elif stype == GeomAbs_Cylinder:
            return "cylinder"
        return "other"

    def get_cylinder_data(self, face: Any) -> Optional[CylinderData]:
        if face is None or self.get_surface_type(face) != "cylinder":
            return None
        surf = BRepAdaptor_Surface(face)
        cyl = surf.Cylinder()
        rad = cyl.Radius()
        pos = cyl.Position()
        loc = pos.Location()
        axis_dir = pos.Direction()

        umin, umax, vmin, vmax = breptools.UVBounds(face)
        height = abs(vmax - vmin)

        return CylinderData(
            radius=rad,
            origin=(loc.X(), loc.Y(), loc.Z()),
            axis=(axis_dir.X(), axis_dir.Y(), axis_dir.Z()),
            height=height
        )

    def get_face_normal(self, face: Any) -> Tuple[float, float, float]:
        if face is None:
            return (0.0, 0.0, 1.0)
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Plane:
            pln = surf.Plane()
            d = pln.Axis().Direction()
            nx, ny, nz = d.X(), d.Y(), d.Z()
            if face.Orientation() == TopAbs_REVERSED:
                return (-nx, -ny, -nz)
            return (nx, ny, nz)
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        cm = props.CentreOfMass()
        # For non-planar surfaces, fall back to axis or Z
        return (0.0, 0.0, 1.0)

    def get_face_adjacencies(self, shape: Any) -> Dict[str, Set[str]]:
        if shape is None or shape.IsNull():
            return {}

        faces = self.get_faces(shape)
        adj: Dict[str, Set[str]] = {str(i): set() for i in range(len(faces))}

        # Build edge to faces map
        edge_to_faces: Dict[int, List[str]] = {}
        for i, face in enumerate(faces):
            exp = TopExp_Explorer(face, TopAbs_EDGE)
            while exp.More():
                edge = exp.Current()
                try:
                    edge_hash = hash(edge)
                except Exception:
                    edge_hash = getattr(edge, 'HashCode', getattr(edge, 'Hash', id))(1000000)
                edge_to_faces.setdefault(edge_hash, []).append(str(i))
                exp.Next()

        for edge_hash, face_list in edge_to_faces.items():
            if len(face_list) >= 2:
                for f1 in face_list:
                    for f2 in face_list:
                        if f1 != f2:
                            adj[f1].add(f2)

        return adj

    def is_valid(self, shape: Any) -> bool:
        return shape is not None and not shape.IsNull()

    def fuse(self, shape1: Any, shape2: Any) -> Any:
        if shape1 is None or shape1.IsNull():
            return shape2
        if shape2 is None or shape2.IsNull():
            return shape1
        algo = BRepAlgoAPI_Fuse(shape1, shape2)
        algo.Build()
        return algo.Shape() if algo.IsDone() else shape1

    def cut(self, shape1: Any, shape2: Any) -> Any:
        if shape1 is None or shape1.IsNull():
            return None
        if shape2 is None or shape2.IsNull():
            return shape1
        algo = BRepAlgoAPI_Cut(shape1, shape2)
        algo.Build()
        return algo.Shape() if algo.IsDone() else shape1

    def get_edge_curve_type(self, edge: Any) -> str:
        """Returns 'circle', 'line', 'other', or 'unknown'."""
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.GeomAbs import GeomAbs_Circle, GeomAbs_Line
        try:
            curve = BRepAdaptor_Curve(edge)
            t = curve.GetType()
            if t == GeomAbs_Circle:
                return "circle"
            elif t == GeomAbs_Line:
                return "line"
            else:
                return "other"
        except Exception:
            return "unknown"

    def min_distance(self, shape_a: Any, shape_b: Any) -> Optional[float]:
        if shape_a is None or shape_b is None:
            return None
        try:
            if hasattr(shape_a, "IsNull") and shape_a.IsNull():
                return None
            if hasattr(shape_b, "IsNull") and shape_b.IsNull():
                return None
            extrema = BRepExtrema_DistShapeShape(shape_a, shape_b)
            extrema.Perform()
            if not extrema.IsDone():
                return None
            return float(extrema.Value())
        except Exception as e:
            logger.error(f"min_distance exception: {e}")
            return None

    def section_overlap_length(self, shape_a: Any, shape_b: Any) -> Optional[float]:
        if shape_a is None or shape_b is None:
            return None
        try:
            if hasattr(shape_a, "IsNull") and shape_a.IsNull():
                return None
            if hasattr(shape_b, "IsNull") and shape_b.IsNull():
                return None
            sec = BRepAlgoAPI_Section(shape_a, shape_b)
            sec.Build()
            if not sec.IsDone():
                return None
            sec_shape = sec.Shape()
            if sec_shape is None or sec_shape.IsNull():
                return 0.0
            exp = TopExp_Explorer(sec_shape, TopAbs_EDGE)
            total_len = 0.0
            while exp.More():
                edge = exp.Current()
                props = GProp_GProps()
                brepgprop.LinearProperties(edge, props)
                total_len += props.Mass()
                exp.Next()
            return float(total_len)
        except Exception as e:
            logger.error(f"section_overlap_length exception: {e}")
            return None
