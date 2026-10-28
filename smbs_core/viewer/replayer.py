"""
replayer.py - Step-by-step sequence replayer for viewer (Stage 4, S.13a).

Scope: iterate candidate_sequence, apply backend.rotate_subtree + FoldState.apply_unfold,
yield StepView per step. Honest marking: geometry_precision="PREVIEW_ONLY" because
PanelGraph has no face_indices populated (see KL-12 in docs/PROGRESS.md); backend
falls back to whole-part rotation. Not a physical re-simulation.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from smbs_core.Core.fold_state import FoldState
# Optional import: panel_face_resolver has top-level OCC imports. Headless envs
# (plain python3, CI without OCC) must import replayer safely — same pattern as
# load_brep's lazy OCC import (line 65). When unavailable, replay_backward
# degrades to PREVIEW_ONLY (whole-part rotation). Narrowed to OCC-only to avoid
# swallowing unrelated ImportError bugs.
try:
    from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
except ModuleNotFoundError as e:
    if (e.name or "").split(".")[0] != "OCC":
        raise
    resolve_panel_faces = None
except ImportError:
    raise
from smbs_core.viewer.graph_builder import ViewerGraphs
from smbs_core.viewer.run_loader import RunArtifacts


GEOMETRY_PRECISION_PREVIEW = "PREVIEW_ONLY"
GEOMETRY_PRECISION_VALID = "VALID_PER_PANEL"
GEOMETRY_WARNING_PREVIEW = (
    "Kinematic preview (whole-part rotation); not a physical re-simulation. "
    "PanelGraph has no face_indices populated from 04_canonical_part (see KL-12)."
)
GEOMETRY_WARNING_PARTIAL = (
    "Partial per-panel kinematic preview: faces assigned to panels where "
    "possible, but some faces remain unassigned (bridge caps / cross-bend) "
    "and stay stationary during unfold — see KL-12 v3."
)


class ReplayerError(Exception):
    """Raised when replayer cannot proceed (missing BREP, unknown bend, rotate failure, etc)."""


@dataclass(frozen=True)
class StepView:
    step_index: int                       # 0 = initial (fully folded), 1..N = after unfold
    bend_id: Optional[str]                # None for step 0
    state: FoldState
    ui_direction: Optional[str]           # "UP"|"DOWN" from direction_by_bend; None for step 0
    planner_status: Optional[str]         # from candidate_sequence[i]; None for step 0
    collision_status: Optional[str]
    tooling_status: Optional[str]
    alignment_status: Optional[str]
    geometry_precision: str               # GEOMETRY_PRECISION_PREVIEW
    geometry_warning: str                 # GEOMETRY_WARNING_PREVIEW


def load_brep(path: Path) -> Any:
    """Load TopoDS_Shape from .brep file via breptools.Read. Raises ReplayerError on failure."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ReplayerError(f"BREP file not found: {file_path}")

    try:
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.BRepTools import breptools
        from OCC.Core.TopoDS import TopoDS_Shape
    except ImportError as e:
        raise ReplayerError(f"OCC dependencies unavailable for loading BREP: {e}") from e

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    try:
        success = breptools.Read(shape, str(file_path), builder)
    except Exception as e:
        raise ReplayerError(f"Failed to read BREP file {file_path}: {e}") from e

    if not success or shape.IsNull():
        raise ReplayerError(f"breptools.Read failed or produced null shape from {file_path}")

    return shape


def _is_valid_solid(shape: Any) -> bool:
    """Check TopoDS_Shape is a single valid solid via BRepCheck_Analyzer.

    Returns False if shape is None/Null, not exactly 1 solid, or BRepCheck invalid.
    Import-guarded for OCC availability (same pattern as load_brep).
    """
    if shape is None:
        return False
    try:
        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.TopAbs import TopAbs_SOLID
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.BRepCheck import BRepCheck_Analyzer
    except ImportError:
        return False
    if not isinstance(shape, TopoDS_Shape) or shape.IsNull():
        return False
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    n_solids = 0
    while exp.More():
        n_solids += 1
        exp.Next()
    if n_solids != 1:
        return False
    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:
        return False


def replay_backward(
    artifacts: RunArtifacts,
    graphs: ViewerGraphs,
    backend: Any,
    initial_shape: Optional[Any] = None,
) -> Iterator[StepView]:
    """
    Backward unfold replayer.

    Yield sequence:
      StepView(0, None, FoldState.initial(...), None, None, None, None, None,
               PREVIEW_ONLY, WARNING)
      For each seq[i] with bend_id=bid:
          axis_origin, axis_dir = state.bend_axes[bid]
          moving_panels = panel_graph.moving_subtree(bid)
          unfolded = backend.rotate_subtree(shape=state.shape, axis_origin=..., axis_direction=...,
                                            signed_angle_deg=-bend_rec.signed_angle,
                                            moving_panels=moving_panels, panel_graph=panel_graph)
          state = state.apply_unfold(bid, unfolded, moving_panels)
          yield StepView(i+1, bid, state, direction_by_bend[bid],
                         seq[i]["planner_status"], ..., PREVIEW_ONLY, WARNING)

    Raises ReplayerError on:
      - initial_shape is None AND geometry_shape_path unreadable
      - rotate_subtree returns None or unchanged shape
      - bend_id from seq not in bend_graph (defensive; graph_builder should have caught)
      - missing required status fields in candidate_sequence items
      - after generator exhausts: state.remaining_bends != set() (defensive invariant)

    Empty candidate_sequence: yields ONLY StepView(0). Not an error.
    """
    # 1. Resolve initial 3D shape
    if initial_shape is not None:
        target_shape = initial_shape
    else:
        geom_path = artifacts.geometry_shape_path
        target_shape = load_brep(geom_path)

    # KL-12 v2 integration (Req 2 F.1): assign B-Rep faces to panels before
    # any rotation. Resolver is idempotent (preserves non-empty face_indices)
    # and safely no-ops for non-TopoDS_Shape (mock/initial_shape path).
    if resolve_panel_faces is not None:
        try:
            resolve_panel_faces(
                target_shape,
                graphs.panel_graph,
                artifacts.canonical_part["payload"],
            )
        except (ValueError, KeyError) as err:
            raise ReplayerError(
                f"Panel face resolution failed before replay: {err}"
            ) from err

    # 3-state honest precision:
    #   no faces assigned      -> PREVIEW_ONLY + original warning (whole-part rotation)
    #   some faces assigned    -> PREVIEW_ONLY + PARTIAL warning (per-panel where possible,
    #                             unassigned faces stationary during unfold)
    #   all faces assigned     -> VALID_PER_PANEL, no warning
    try:
        from OCC.Core.TopoDS import TopoDS_Shape
        is_topods = isinstance(target_shape, TopoDS_Shape)
    except ImportError:
        is_topods = False

    if not is_topods:
        precision = GEOMETRY_PRECISION_PREVIEW
        warning: Optional[str] = GEOMETRY_WARNING_PREVIEW
    else:
        total_faces = len(backend.get_faces(target_shape)) if hasattr(backend, "get_faces") else 0
        assigned_indices: set = set()
        for p in graphs.panel_graph.panels.values():
            assigned_indices.update(p.face_indices)
        n_assigned = len(assigned_indices)
        if total_faces > 0 and n_assigned == total_faces:
            precision = GEOMETRY_PRECISION_VALID
            warning = None
        elif n_assigned > 0:
            precision = GEOMETRY_PRECISION_PREVIEW
            warning = GEOMETRY_WARNING_PARTIAL
        else:
            precision = GEOMETRY_PRECISION_PREVIEW
            warning = GEOMETRY_WARNING_PREVIEW

    # 2. Extract candidate sequence
    try:
        candidate_seq = artifacts.search_result["payload"]["candidate_sequence"]
    except (KeyError, TypeError) as err:
        raise ReplayerError(f"Missing 'candidate_sequence' in search_result payload: {err}") from err

    # N=1 disk-state path (P1a): use existing physical solids, no rigid rotation.
    # For single-bend parts, flat_pattern.brep IS the target state 1; loading it
    # avoids the rotate_subtree → Sewing → torn-shells failure (KL-12 v3).
    if candidate_seq and len(candidate_seq) == 1 and artifacts.flat_pattern_path is not None:
        try:
            flat_shape = load_brep(artifacts.flat_pattern_path)
        except ReplayerError:
            flat_shape = None

        if _is_valid_solid(flat_shape) and _is_valid_solid(target_shape):
            # Both disk states are single valid solids → honest VALID_PER_PANEL.
            precision = GEOMETRY_PRECISION_VALID
            warning = None

            bid = candidate_seq[0]["bend_id"]
            for fld in ("planner_status", "collision_status", "tooling_status", "alignment_status"):
                if fld not in candidate_seq[0]:
                    raise ReplayerError(
                        f"Step 1 for bend '{bid}' is missing required status field '{fld}'"
                    )
            bend_rec = graphs.bend_graph.get_bend(bid)
            if bend_rec is None:
                raise ReplayerError(f"bend_id '{bid}' not in bend_graph")
            moving_panels = graphs.panel_graph.moving_subtree(bid)
            if not moving_panels:
                raise ReplayerError(f"Empty moving_subtree for bend '{bid}'")
            try:
                ui_dir = graphs.direction_by_bend[bid]
            except KeyError as ke:
                raise ReplayerError(f"bend_id '{bid}' missing from direction_by_bend") from ke

            current_state = FoldState.initial(
                part_shape=target_shape,
                bend_graph=graphs.bend_graph,
                panel_graph=graphs.panel_graph,
            )
            yield StepView(
                step_index=0, bend_id=None, state=current_state,
                ui_direction=None, planner_status=None,
                collision_status=None, tooling_status=None, alignment_status=None,
                geometry_precision=precision, geometry_warning=warning,
            )

            current_state = current_state.apply_unfold(bid, flat_shape, moving_panels)
            yield StepView(
                step_index=1, bend_id=bid, state=current_state,
                ui_direction=ui_dir,
                planner_status=candidate_seq[0]["planner_status"],
                collision_status=candidate_seq[0]["collision_status"],
                tooling_status=candidate_seq[0]["tooling_status"],
                alignment_status=candidate_seq[0]["alignment_status"],
                geometry_precision=precision, geometry_warning=warning,
            )
            return

    # N >= 2 constructive stepwise path (P1b/P1b-extended): use SheetMetalFolder prefix folding.
    # Yields genuine B-Rep solids at steps 0, 1, ..., N for multi-bend sequences (KL-12 v3).
    # Lazy-imported to keep replayer.py safe in headless envs (plain python3 without FreeCAD Mod).
    if candidate_seq and len(candidate_seq) >= 2 and artifacts.flat_pattern_path is not None and artifacts.canonical_part is not None:
        try:
            from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
            _folder_cls: Optional[Any] = SheetMetalFolder
        except (ImportError, ModuleNotFoundError):
            _folder_cls = None

        if _folder_cls is not None:
            try:
                flat_shape = load_brep(artifacts.flat_pattern_path)
            except ReplayerError:
                flat_shape = None

            if flat_shape is not None and _is_valid_solid(flat_shape):
                # Validate required sequence fields defensively
                for step_idx, step_data in enumerate(candidate_seq):
                    bid = step_data.get("bend_id")
                    for fld in ("planner_status", "collision_status", "tooling_status", "alignment_status"):
                        if fld not in step_data:
                            raise ReplayerError(
                                f"Step {step_idx + 1} for bend '{bid}' is missing required status field '{fld}'"
                            )
                    bend_rec = graphs.bend_graph.get_bend(bid)
                    if bend_rec is None:
                        raise ReplayerError(f"bend_id '{bid}' not in bend_graph")
                    moving_panels = graphs.panel_graph.moving_subtree(bid)
                    if not moving_panels:
                        raise ReplayerError(f"Empty moving_subtree for bend '{bid}'")
                    if bid not in graphs.direction_by_bend:
                        raise ReplayerError(f"bend_id '{bid}' missing from direction_by_bend")

                # Constructive stepwise generation with per-step precision (P1b / Stage 1 P0)
                try:
                    s0_shape = _folder_cls.fold_from_canonical_part(artifacts.canonical_part, flat_shape)
                except Exception:
                    s0_shape = None

                if s0_shape is not None:
                    step_shapes = []
                    step_precisions = []
                    step_warnings = []

                    # Step 0: Fully folded 3D part
                    if _is_valid_solid(s0_shape):
                        step_shapes.append(s0_shape)
                        step_precisions.append(GEOMETRY_PRECISION_VALID)
                        step_warnings.append(None)
                    elif target_shape is not None and _is_valid_solid(target_shape):
                        step_shapes.append(target_shape)
                        step_precisions.append(GEOMETRY_PRECISION_VALID)
                        step_warnings.append(None)
                    else:
                        step_shapes.append(s0_shape)
                        step_precisions.append(GEOMETRY_PRECISION_PREVIEW)
                        step_warnings.append("Step 0 shape is not a verified monolithic solid")

                    fold_tracker = FoldState.initial(
                        part_shape=step_shapes[0],
                        bend_graph=graphs.bend_graph,
                        panel_graph=graphs.panel_graph,
                    )

                    # Intermediate steps 1..N-1
                    cp_prog = copy.deepcopy(artifacts.canonical_part)
                    cp_payload = cp_prog.get("payload", cp_prog)
                    bends_dict = cp_payload.get("bends", {})

                    for step_idx, step_data in enumerate(candidate_seq[:-1]):
                        bid = step_data["bend_id"]
                        if bid in bends_dict:
                            bend_entry = bends_dict[bid]
                            if isinstance(bend_entry.get("angle_deg"), dict):
                                bend_entry["angle_deg"]["value"] = 0.0
                            else:
                                bend_entry["angle_deg"] = 0.0
                        try:
                            s_k = _folder_cls.fold_from_canonical_part(cp_prog, flat_shape)
                        except Exception:
                            s_k = None

                        if s_k is not None and _is_valid_solid(s_k):
                            step_shapes.append(s_k)
                            step_precisions.append(GEOMETRY_PRECISION_VALID)
                            step_warnings.append(None)
                        else:
                            # Non-monolithic or folding failed on this step
                            if s_k is None:
                                try:
                                    axis_origin, axis_dir = fold_tracker.bend_axes[bid]
                                    moving_panels = graphs.panel_graph.moving_subtree(bid)
                                    bend_rec = graphs.bend_graph.get_bend(bid)
                                    signed_angle = bend_rec.signed_angle
                                    s_k = backend.rotate_subtree(
                                        shape=step_shapes[-1],
                                        axis_origin=axis_origin,
                                        axis_direction=axis_dir,
                                        signed_angle_deg=-signed_angle,
                                        moving_panels=moving_panels,
                                        panel_graph=graphs.panel_graph,
                                    )
                                except Exception:
                                    s_k = None
                            if s_k is None:
                                s_k = step_shapes[-1]

                            step_shapes.append(s_k)
                            step_precisions.append(GEOMETRY_PRECISION_PREVIEW)
                            step_warnings.append(
                                f"Intermediate step {step_idx + 1} for bend '{bid}' is not a verified monolithic solid (preview only)"
                            )

                        moving_panels = graphs.panel_graph.moving_subtree(bid)
                        fold_tracker = fold_tracker.apply_unfold(bid, step_shapes[-1], moving_panels)

                    # Final step N: flat pattern
                    step_shapes.append(flat_shape)
                    if _is_valid_solid(flat_shape):
                        step_precisions.append(GEOMETRY_PRECISION_VALID)
                        step_warnings.append(None)
                    else:
                        step_precisions.append(GEOMETRY_PRECISION_PREVIEW)
                        step_warnings.append("Flat pattern shape is not a verified monolithic solid")

                    # Yield all StepViews with per-step precision and diagnostic
                    current_state = FoldState.initial(
                        part_shape=step_shapes[0],
                        bend_graph=graphs.bend_graph,
                        panel_graph=graphs.panel_graph,
                    )
                    yield StepView(
                        step_index=0, bend_id=None, state=current_state,
                        ui_direction=None, planner_status=None,
                        collision_status=None, tooling_status=None, alignment_status=None,
                        geometry_precision=step_precisions[0],
                        geometry_warning=step_warnings[0],
                    )

                    for step_idx, step_data in enumerate(candidate_seq):
                        bid = step_data["bend_id"]
                        moving_panels = graphs.panel_graph.moving_subtree(bid)
                        ui_dir = graphs.direction_by_bend[bid]
                        next_shape = step_shapes[step_idx + 1]

                        current_state = current_state.apply_unfold(bid, next_shape, moving_panels)
                        yield StepView(
                            step_index=step_idx + 1,
                            bend_id=bid,
                            state=current_state,
                            ui_direction=ui_dir,
                            planner_status=step_data["planner_status"],
                            collision_status=step_data["collision_status"],
                            tooling_status=step_data["tooling_status"],
                            alignment_status=step_data["alignment_status"],
                            geometry_precision=step_precisions[step_idx + 1],
                            geometry_warning=step_warnings[step_idx + 1],
                        )
                    return

    # Fallback: N >= 2 or N=1 disk path failed → existing rigid rotate_subtree loop.
    current_state = FoldState.initial(
        part_shape=target_shape,
        bend_graph=graphs.bend_graph,
        panel_graph=graphs.panel_graph
    )

    # Yield step 0
    yield StepView(
        step_index=0,
        bend_id=None,
        state=current_state,
        ui_direction=None,
        planner_status=None,
        collision_status=None,
        tooling_status=None,
        alignment_status=None,
        geometry_precision=precision,
        geometry_warning=warning
    )

    if not candidate_seq:
        return

    required_status_fields = ("planner_status", "collision_status", "tooling_status", "alignment_status")

    for i, step_info in enumerate(candidate_seq):
        try:
            bid = step_info["bend_id"]
        except (KeyError, TypeError) as err:
            raise ReplayerError(f"Step {i+1} in candidate_sequence is missing 'bend_id': {err}") from err
        if not bid:
            raise ReplayerError(f"Step {i+1} in candidate_sequence has empty 'bend_id'")

        bend_rec = graphs.bend_graph.get_bend(bid)
        if bend_rec is None:
            raise ReplayerError(f"bend_id '{bid}' from candidate_sequence not found in bend_graph")

        for fld in required_status_fields:
            if fld not in step_info:
                raise ReplayerError(f"Step {i+1} for bend '{bid}' is missing required status field '{fld}'")

        # Dynamic bend axes from current state
        if bid not in current_state.bend_axes:
            raise ReplayerError(f"Current fold state does not contain dynamic axis for bend '{bid}'")
        axis_origin, axis_dir = current_state.bend_axes[bid]

        # Moving subtree from panel graph
        moving_panels = graphs.panel_graph.moving_subtree(bid)
        if not moving_panels:
            raise ReplayerError(f"Panel graph returned empty moving_subtree for bend '{bid}'")

        # Unfold rotation: -signed_angle
        signed_angle = bend_rec.signed_angle
        unfold_angle = -signed_angle

        try:
            unfolded = backend.rotate_subtree(
                shape=current_state.shape,
                axis_origin=axis_origin,
                axis_direction=axis_dir,
                signed_angle_deg=unfold_angle,
                moving_panels=moving_panels,
                panel_graph=graphs.panel_graph
            )
        except Exception as e:
            raise ReplayerError(f"Backend rotate_subtree failed on step {i+1} for bend '{bid}': {e}") from e

        if unfolded is None:
            raise ReplayerError(f"rotate_subtree returned None for bend '{bid}' on step {i+1}; backend may be misconfigured")

        # Defensive check against identity (no transformation took place)
        if unfolded is current_state.shape:
            raise ReplayerError(f"rotate_subtree returned unchanged shape identity for bend '{bid}' on step {i+1}")

        # Apply unfold state transition
        current_state = current_state.apply_unfold(bid, unfolded, moving_panels)

        # UI direction from graph_builder direction_by_bend map
        try:
            ui_dir = graphs.direction_by_bend[bid]
        except KeyError as ke:
            raise ReplayerError(f"bend_id '{bid}' missing from direction_by_bend mapping: {ke}") from ke

        yield StepView(
            step_index=i + 1,
            bend_id=bid,
            state=current_state,
            ui_direction=ui_dir,
            planner_status=step_info["planner_status"],
            collision_status=step_info["collision_status"],
            tooling_status=step_info["tooling_status"],
            alignment_status=step_info["alignment_status"],
            geometry_precision=precision,
            geometry_warning=warning
        )

    # 4. Final defensive invariant: after completing full sequence, remaining_bends must be empty
    if current_state.remaining_bends:
        raise ReplayerError(
            f"Candidate sequence exhausted but remaining_bends is not empty: {sorted(list(current_state.remaining_bends))}"
        )
