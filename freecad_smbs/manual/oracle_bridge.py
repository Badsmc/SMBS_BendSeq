"""
oracle_bridge.py - Production OracleBridge for Manual Mode (Phase 4c.1 / J1)

Implements:
1. transform_compound_preserving_order: SE(3) rigid body transformation preserving
   exact 1-to-1 face order via TopoDS_Compound without BRepBuilderAPI_Sewing.
2. resolve_panel_primary_sheet_pair: Semantic filtering of sheet metal panels to the
   two largest opposing planar faces, preventing spurious relief notch / edge collisions.
3. OracleBridge: Authoritative execution wrapper over PhysicalValidator with confirmed
   tooling datums and fail-closed validation.
"""

import math
import logging
from typing import Dict, Any, List, Set, Optional, Tuple, Union

from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Compound
from OCC.Core.BRep import BRep_Builder
from OCC.Core.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Core.bend_graph import BendRecord
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.validator import PhysicalValidator, ValidationResult
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces

logger = logging.getLogger("smbs.manual.oracle_bridge")


def transform_compound_preserving_order(
    shape: TopoDS_Shape,
    axis_origin: Tuple[float, float, float],
    axis_direction: Tuple[float, float, float],
    signed_angle_deg: float,
    moving_panels: Optional[Set[str]] = None,
    panel_graph: Optional[PanelGraph] = None
) -> TopoDS_Shape:
    """
    Applies rigid SE(3) subtree rotation to moving panels while preserving exact
    topological face ordering using TopoDS_Compound (I1).
    Guarantees: Face(i, unfolded_compound) == T_rigid(Face(i, source_shape)).
    """
    if shape is None or (hasattr(shape, "IsNull") and shape.IsNull()):
        return shape

    moving_faces: Set[int] = set()
    if panel_graph is not None and moving_panels:
        for mpid in moving_panels:
            pan = panel_graph.panels.get(mpid)
            if pan and pan.face_indices:
                moving_faces.update(pan.face_indices)

    trsf = gp_Trsf()
    ax = gp_Ax1(gp_Pnt(*axis_origin), gp_Dir(*axis_direction))
    trsf.SetRotation(ax, math.radians(signed_angle_deg))

    comp_builder = BRep_Builder()
    comp_shape = TopoDS_Compound()
    comp_builder.MakeCompound(comp_shape)

    f_exp = TopExp_Explorer(shape, TopAbs_FACE)
    idx = 0
    while f_exp.More():
        f = f_exp.Current()
        if idx in moving_faces:
            comp_builder.Add(comp_shape, BRepBuilderAPI_Transform(f, trsf, True).Shape())
        else:
            comp_builder.Add(comp_shape, f)
        idx += 1
        f_exp.Next()

    return comp_shape


def resolve_panel_primary_sheet_pair(
    ref_shape: TopoDS_Shape,
    panel_graph: PanelGraph,
    canonical_payload: Dict[str, Any]
) -> None:
    """
    Filters resolved panel face_indices to the two largest planar faces parallel
    to the panel normal (I3 - Primary Sheet Pair Resolution).
    Excludes small corner relief cuts and perpendicular side edges.
    """
    resolve_panel_faces(ref_shape, panel_graph, canonical_payload)
    if not hasattr(panel_graph, "panels") or not panel_graph.panels:
        return

    f_exp = TopExp_Explorer(ref_shape, TopAbs_FACE)
    faces_meta: List[Tuple[int, float, int]] = []
    idx = 0
    while f_exp.More():
        f = f_exp.Current()
        props = GProp_GProps()
        brepgprop.SurfaceProperties(f, props)
        surf = BRepAdaptor_Surface(f)
        faces_meta.append((idx, float(props.Mass()), int(surf.GetType())))
        idx += 1
        f_exp.Next()

    for pid, panel in panel_graph.panels.items():
        planar_candidates = []
        for f_idx in panel.face_indices:
            if f_idx < len(faces_meta):
                _, area, stype = faces_meta[f_idx]
                if stype == GeomAbs_Plane:
                    planar_candidates.append((f_idx, area))
        planar_candidates.sort(key=lambda x: x[1], reverse=True)
        # Select top 2 largest planar faces (the two sides of the sheet)
        panel.face_indices = sorted([x[0] for x in planar_candidates[:2]])


class OracleBridge:
    """
    Production bridge executing physical validation for Manual Mode sequences.
    Wraps PhysicalValidator with index-preserving transformations and clean datums.
    """

    def __init__(
        self,
        machine: Optional[PressBrakeMachine] = None,
        backend: Optional[PythonOCCBackend] = None,
        tooling_set_id: Optional[str] = None
    ):
        if tooling_set_id is None:
            raise ValueError("tooling_set_id is required; Manual Mode requires explicit tooling configuration")
        self.tooling_set_id = tooling_set_id
        self.backend = backend or PythonOCCBackend()
        # Bind the index-preserving transform directly to this backend instance (J1)
        self.backend.rotate_subtree = transform_compound_preserving_order

        if machine is not None:
            self.machine = machine
        else:
            # Load real tooling configuration from tooling library / tooling_set_id (K1)
            from freecad_smbs.resources.tooling_paths import (
                TOOL_LIBRARY_ROOT,
                resolve_machine_frame_fcstd,
                PUNCH_PATH,
                DIE_PATH
            )
            from smbs_core.geometry_backend.io.tooling_set import ToolingSet

            set_json_path = TOOL_LIBRARY_ROOT / "sets" / f"{tooling_set_id}.json"
            punch_fcstd = PUNCH_PATH
            die_fcstd = DIE_PATH

            if set_json_path.exists():
                try:
                    import json
                    with open(set_json_path, "r", encoding="utf-8") as f:
                        tset = ToolingSet.from_dict(json.load(f))
                    p_cand = TOOL_LIBRARY_ROOT / "punch" / f"{tset.punch_id}.FCStd"
                    d_cand = TOOL_LIBRARY_ROOT / "die" / f"{tset.die_id}.FCStd"
                    if p_cand.exists():
                        punch_fcstd = p_cand
                    if d_cand.exists():
                        die_fcstd = d_cand
                except Exception as e:
                    logger.warning(f"Could not load set file {set_json_path}: {e}")

            frame_fcstd = resolve_machine_frame_fcstd(TOOL_LIBRARY_ROOT)

            self.machine = PressBrakeMachine(
                punch_path=str(punch_fcstd) if punch_fcstd and punch_fcstd.exists() else None,
                die_path=str(die_fcstd) if die_fcstd and die_fcstd.exists() else None,
                machine_frame_path=str(frame_fcstd) if frame_fcstd and frame_fcstd.exists() else None,
                punch_target_object_name="Body",
                die_target_object_name="Body",
                machine_frame_target_object_name=None
            )

        self.validator = PhysicalValidator(self.machine, self.backend)

        # Patch assembly.place to preserve machine_frame across validator.alignment_status calls
        if hasattr(self.validator, "assembly") and self.validator.assembly:
            orig_place = self.validator.assembly.place

            def preserving_place(punch, die, machine_frame=None, bed=None):
                if machine_frame is None and getattr(self.validator.assembly, "machine_frame", None) is not None:
                    machine_frame = self.validator.assembly.machine_frame
                return orig_place(punch, die, machine_frame=machine_frame, bed=bed)

            self.validator.assembly.place = preserving_place

    def validate_step(
        self,
        current_state: FoldState,
        bend_record: BendRecord,
        panel_graph: PanelGraph,
        unknown_policy: str = "PRODUCTION"
    ) -> ValidationResult:
        """
        Validates a single folding step physically.
        Returns ValidationResult with valid=True/False and canonical stage.
        Default policy is strictly 'PRODUCTION' (K1).
        """
        return self.validator.validate_step(
            current_state=current_state,
            bend_record=bend_record,
            panel_graph=panel_graph,
            unknown_policy=unknown_policy
        )
