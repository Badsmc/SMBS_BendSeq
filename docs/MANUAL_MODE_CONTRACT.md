# MANUAL_MODE_CONTRACT.md — Architecture & Contracts for SMBS HandMod

Status: APPROVED ARCHITECTURE SPECIFICATION (Gate 0 Finalized)  
Target Branch: `HandMod-V.0.0.1`  
Scope: Interactive manual bend sequencing in FreeCAD without combinatorial search algorithms (DFS / A* / Greedy).

---

## 1. Executive Summary & Flow

HandMod replaces combinatorial search planners (`AStarBackwardPlanner`, `DFSBackwardPlanner`, `GreedyUnfoldPlanner`) with explicit user intent:
1. User unfolds sheet metal in FreeCAD (`SheetMetal_Unfold` or existing 3D Unfold object / TechDraw 2D projection).
2. User selects bend lines in desired execution order via `ManualSequencePanel` (TaskPanel).
3. `ManualSequenceWorker` coordinates a dedicated mini-pipeline: generates `01_input_manifest.json` → `02_geometry_result.json` via `GeometryWorker` → `04_canonical_part.json` via `TopologyWorker`.
4. System executes deterministic mapping from selected FreeCAD edge to canonical `BendRecord` via `04_canonical_part.json`.
5. System determines rigid alignment transformation $T_{4\times4}$ between 2D drawing / 3D unfold coordinates and 3D bent model.
6. `ManualSequenceWorker` evaluates the user's fixed sequence step-by-step through **Physical Oracle** (`PhysicalValidator`).
7. Upon validation pass (`ok=True`), standard SMBS run artifacts (`07_search_result.json`, `09_verification_result.json`, `10_part_bendseq.json`) are generated, and bend sequence numbers ("1", "2", "3"...) are rendered as 3D / 2D annotations.

---

## 2. Existing Modules Map & Integration Points

| Existing Module / Path | Public API / Symbols | Role in HandMod |
|---|---|---|
| `smbs_core.coordinator.run_pipeline` | `Coordinator.start_run(source_file_path, input_mode, ...)` | Reference for full artifact lifecycle (01 through 11). HandMod produces compliant run artifacts without calling search planners. |
| `freecad_smbs.integration.smbs_bridge` | `run_pipeline_on_fcstd(...)`, `PipelineRunResult`, `RunArtifacts` | AutoMod bridge. HandMod provides sibling entry point `run_manual_sequence_on_fcstd(...)`. |
| `smbs_core.Physics.validator` | `PhysicalValidator.validate_step(current_state, bend_record, panel_graph, ...)` | **Physical Oracle**. Evaluates collisions (APPROACH, KINEMATICS, SELF, TOOL, MACHINE, RETRACT) for each manual step. |
| `smbs_core.Physics.backgauge_calculator` | `BackgaugeCalculator.compute_step_backgauge(...)` | Computes $X, R, Z_1, Z_2$ backgauge stop coordinates for each manual step. |
| `smbs_core.Core.fold_state` | `FoldState.initial(target_shape, bend_graph, panel_graph)`, `val_res.next_state` | State representation of the bent part after each step. |
| `smbs_core.workers.topology.worker` | `TopologyWorker.execute(...)` | Extracts canonical panels, bends (`BendRecord`), and computes `04_canonical_part.json`. |
| `freecad_smbs.ui.annotations` | `render_flat_pattern_annotations(...)`, `compute_annotation_positions(...)` | Renders `App::AnnotationLabel` in FreeCAD 3D scene. |
| `freecad_smbs.ui.manual_sequence_panel` | `ManualSequencePanel` | FreeCAD TaskPanel for edge selection, reordering, and triggering "Проверить" / "Рассчитать". |

---

## 3. § Mapping Chain: `Edge → bend_id → BendRecord` (Answers Q1 & Q6)

### 3.1 Mapping Architecture: Approach (A) — Canonical Anchor
In HandMod, `bend_id` is **always the canonical ID** defined in `04_canonical_part.json` (e.g. `BEND_01`, `BEND_02`).
The user clicks on an Edge in FreeCAD (either on the 3D Unfold solid or on the 3D bent part).

```
FreeCAD Selection: (Object, Edge)
       │
       ▼
[Edge Geometry Inspection]
   - Midpoint P_mid (x, y, z)
   - Tangent Direction V_dir (dx, dy, dz)
   - Edge Length L_edge
   - Adjacent Face normal N_face (if available)
       │
       ▼
[Match against 04_canonical_part.json]
   Canonical bend entry has:
   - bend_line_flat_pattern: {start_mm, end_mm} (on flat pattern)
   - 3D axis_point, axis_direction, length_mm, radius_mm (on bent part)
       │
       ▼
[Normalized Scoring Function]
   Score ∈ [0, 1] = w1 * (dist / dist_max) + w2 * (1.0 - |V_dir · V_cand|) + w3 * (1.0 - |N_face · N_cand|)
   where w1=0.5, w2=0.3, w3=0.2 (w1+w2+w3=1.0)
       │
       ▼
Canonical bend_id (e.g. "BEND_01")
       │
       ▼
BendRecord (loaded directly from bend_graph / canonical_bends dict)
```

### 3.2 Normalized Score & Tie-Break Rule (Q6, Y3)
To prevent accidental misidentification when bends are closely spaced (e.g. 5–10 mm apart with $R=2$):
1. **Candidate Filtering:** candidate length must match edge length within $\pm 5\%$.
2. **Normalized Composite Score:**
   $$\text{Score} = w_1 \cdot \min\left(1.0, \frac{\|P_{\text{edge}} - P_{\text{cand}}\|}{d_{\max}}\right) + w_2 \cdot (1.0 - |\vec{V}_{\text{edge}} \cdot \vec{V}_{\text{cand}}|) + w_3 \cdot (1.0 - |\vec{N}_{\text{edge}} \cdot \vec{N}_{\text{cand}}|)$$
   where $d_{\max} = 10.0\text{ mm}$, $w_1 = 0.5$, $w_2 = 0.3$, $w_3 = 0.2$ ($\sum w_i = 1.0$, $\text{Score} \in [0.0, 1.0]$).
3. **Tie-Break:** If multiple candidates have $|\text{Score}_A - \text{Score}_B| < 0.1$, tie-break deterministically using lexicographic tuple:
   $$(\text{Score},\; |\text{radius}_{\text{edge}} - \text{radius}_{\text{cand}}|,\; \text{adjacent\_panel\_id},\; \text{bend\_id})$$
4. If minimum score exceeds $0.4$ (or distance $> 4.0\text{ mm}$), the edge is rejected as non-bend with clear user diagnostic.

---

## 4. § Integration Mode: Architecture of `run_manual_sequence_on_fcstd` (Answers Q2, B1, B3, B4, B5)

### 4.1 Boundary Contract: Option (B) — Worker Does Full Autonomous Resolution
In HandMod, `ManualSequencePanel` is kept **thin** and stores selections with `bend_id: Optional[str] = None` along with immutable `RawBendLineSnapshot` (captured at click time).

The entry point:
```python
def run_manual_sequence_on_fcstd(
    fcstd_path: Union[str, Path],
    manual_input: ManualSequenceInput,
    *,
    out_runs_dir: Union[str, Path, None] = None,
    library_path: Union[str, Path, None] = None,
    tooling_set_id: Optional[str] = None,
    cancel_token: Optional[threading.Event] = None,
    phases: Optional[List[str]] = None,  # e.g. ["04"] for Phase 4a mini-pipeline
) -> ManualSequenceResult:
```

**Worker Execution Flow (One Transaction, Isolated Run Dir):**
1. Runs `GeometryWorker` → generates `01_input_manifest.json` and `02_geometry_result.json` + `geometry.brep` + `flat_pattern.brep`.
2. Runs `TopologyWorker` → generates `04_canonical_part.json`.
3. Resolves each `BendSelectionItem.raw_snapshot` against real `04_canonical_part.json` via `match_to_canonical`:
   - If **all** items resolve with score $\le 0.6$ (or distance $\le 4.0\text{ mm}$): assigns canonical `bend_id` to each item.
   - If **any** item fails to resolve: fails immediately (`ok=False`, `planning_status="ERROR"`), records `unmatched_snapshots: List[RawBendLineSnapshot]` in the result, and logs clear diagnostic without running downstream stages.
4. Validates sequence step-by-step through Physical Oracle (Phases 4c/6) unless restricted by `phases`.

### 4.2 Run Directory Policy (§B4)
- **Default Directory:** `<repo_root>/runs/run_manual_<YYYYMMDD_HHMMSS>_<uuid6>` (consistent with SMBS AutoMod convention).
- **Custom Directory:** If `out_runs_dir` is passed, runs are created under `Path(out_runs_dir).expanduser().resolve() / run_id`.
- **Retention / Cleanup:** Run directories contain complete standard artifacts (01, 02, 04, 07, 09, 10). Automated retention policy cleans up temporary runs exceeding the configured limit (default: keep latest 50 manual runs; overridden via preferences).

### 4.3 Return Type & Downstream Compatibility (§B5)
`run_manual_sequence_on_fcstd` returns `ManualSequenceResult`, defined as:
```python
@dataclass
class ManualSequenceResult:
    ok: bool
    run_dir: Path
    part_id: str
    selections: List[BendSelectionItem]  # with bend_id resolved
    unmatched_snapshots: List[RawBendLineSnapshot] = field(default_factory=list)
    steps: List[OracleStepResult] = field(default_factory=list)
    planning_status: str = "UNKNOWN"      # "SUCCESS", "NO_SOLUTION", "ERROR"
    error_message: Optional[str] = None
    diagnostics: List[str] = field(default_factory=list)
    artifacts: Optional[RunArtifacts] = None

    def to_pipeline_run_result(self) -> PipelineRunResult:
        """Adapter allowing existing VerificationDialog, AnimationPlayerDialog, and UI tooling to operate without modification."""
        ...
```
- The produced artifacts (`04_canonical_part.json`, `07_search_result.json`, `09_verification_result.json`, `10_part_bendseq.json`) are byte-for-byte schema compliant with existing AutoMod.
- `07_search_result.json` populates `candidate_sequence` strictly from `manual_input.selections` in the user's order.
- Existing `VerifierWorker` can process the resulting run directory with zero code changes.

### 4.4 Cancellation Protocol (§Y4)
`cancel_token` is an optional `threading.Event` passed into `ManualSequenceInput` or directly to `run_manual_sequence_on_fcstd`.
- Worker checks `cancel_token.is_set()` between validation steps.
- If cancellation is requested, worker stops further execution, marks `planning_status="CANCELLED"`, and returns a partial result with already executed steps.

### 4.5 Topological Stale Invalidation (§Y5)
To avoid false invalidations when FreeCAD fires document recompute events (e.g. from 3D annotation labels, view rotations, or zoom events):
- `ManualSequencePanel.mark_stale(new_shape)` checks topological shape identity via `new_shape.hashCode()` (or edge count + bounding box).
- Recompute events that leave the `Unfold` shape topology unchanged do NOT invalidate selections.

```
User clicks "Проверить" in ManualSequencePanel
       │
       ▼
[Stage 1: GeometryWorker]
Writes 01_input_manifest.json → 02_geometry_result.json + geometry.brep + flat_pattern.brep
       │
       ▼
[Stage 2: TopologyWorker]
Reads 02_geometry_result.json → Writes 04_canonical_part.json
       │
       ▼
[Stage 3: Edge Matching against 04 (Worker Internal)]
Validates user-selected edges → resolves canonical bend_id list [B_1, B_2, ... B_N]
(Fails-fast with unmatched_snapshots if any bend cannot be mapped)
       │
       ▼
[Stage 4: Coordinate Alignment (Phase 5)]
Extracts 2D/3D contours → computes T_4x4 and T_inv
       │
       ▼
[Stage 5: ManualSequenceSolver & Oracle (Phase 6)]
Validates fixed sequence step-by-step → Writes 07_search_result.json
       │
       ▼
[Stage 6: VerifierWorker & ExportWorker (Phase 6)]
Verifies steps independently (09_verification_result.json) → Writes 10_part_bendseq.json
       │
       ▼
[Stage 7: Render Annotations (Phase 7)]
3D scene labels (App::AnnotationLabel) + TechDraw balloon/labels via T_inv
```

**Decision:** Option (B) from B4 + Option (A) from B1 + Option (B5) Return Adapter:
- Single entry point, single run-dir transaction.
- Panel remains thin and decoupled from FreeCAD worker pipelines.
- Full downstream compatibility with existing Verifier and simulation UI.

---

## 5. § FoldState Lifecycle (Answers Q4)

### 5.1 Verification Loop & State Evolution
The user-defined sequence $[B_1, B_2, \dots, B_N]$ is validated step-by-step on the evolving `FoldState`.

```python
# FoldState Lifecycle in ManualSequenceSolver
state = FoldState.initial(target_shape, bend_graph, panel_graph)
steps_results = []
accumulated_states = [state]

for order_idx, sel in enumerate(input.selections, start=1):
    bend_id = sel.bend_id
    bend_rec = bend_graph.get_bend(bend_id)
    
    # 1. Integrity check: bend must be in remaining_bends of current state
    if bend_id not in state.remaining_bends:
        steps_results.append(OracleStepResult(
            step_order=order_idx,
            bend_id=bend_id,
            valid=False,
            stage="consistency",
            reason=f"Bend {bend_id} is already folded or invalid for remaining set {state.remaining_bends}"
        ))
        break

    # 2. Sequential Backgauge Calculation (Fail-Closed)
    try:
        bg_data = backgauge_calc.compute_step_backgauge(
            step_number=order_idx,
            bend_info={"length": bend_rec.length, "bend_id": bend_id, "geometry_confirmed": False},
            part_thickness=canonical_part["thickness_mm"]["value"],
            confirmed_geometry=False
        )
    except Exception as bg_err:
        steps_results.append(OracleStepResult(
            step_order=order_idx,
            bend_id=bend_id,
            valid=False,
            stage="BACKGAUGE",
            reason=f"Backgauge calculation failed: {bg_err}"
        ))
        break

    # 3. Invoke Physical Oracle (black box)
    val_res = validator.validate_step(
        current_state=state,
        bend_record=bend_rec,
        panel_graph=panel_graph,
        unknown_policy="PRODUCTION"
    )

    if not val_res.valid:
        steps_results.append(OracleStepResult(
            step_order=order_idx,
            bend_id=bend_id,
            valid=False,
            stage=val_res.stage,
            reason=val_res.reason,
            backgauge=bg_data
        ))
        break  # Sequence invalid; stop progression

    # 4. Advance state
    state = val_res.next_state
    accumulated_states.append(state)
    
    steps_results.append(OracleStepResult(
        step_order=order_idx,
        bend_id=bend_id,
        valid=True,
        stage="VALID",
        backgauge=bg_data
    ))
```

---

## 6. § Backgauge Placement (Answers Q5 & Y1)

### Sequential Execution & Fail-Closed Policy:
1. `compute_step_backgauge` is executed **sequentially in the same step loop** right before or after the Oracle step check.
2. **Fail-Closed Guarantee:** If backgauge computation raises an exception or fails critical safety limits, the step is marked `valid=False`, with `stage="BACKGAUGE"`, and execution halts immediately (`ok=False`).
3. Successful backgauge coordinates ($X, R, Z_1, Z_2$) are recorded into `OracleStepResult.backgauge` and serialized into `candidate_sequence[i]["backgauge"]` in `07_search_result.json`.

---

## 7. § Alignment Method & Fallback Chain (Answers Q3 & Y2)

### 7.1 Coordinate Systems & Inverses
- **2D Drawing / TechDraw:** Plane coordinates $P_{2D} = (u, v, 0)$ on sheet/view.
- **3D Unfold:** Cartesian coordinates $P_{3D} = (X, Y, Z)$ on the unfolded solid.
- **Forward Matrix $T_{4\times4}$:** Maps 2D drawing coordinates to 3D unfold coordinates ($P_{3D} = T_{4\times4} \cdot P_{2D}$).
- **Inverse Matrix $T^{-1}_{4\times4}$:** Maps 3D unfold coordinates back to 2D TechDraw sheet coordinates ($P_{2D} = T^{-1}_{4\times4} \cdot P_{3D}$).

### 7.2 Transformation Fallback Chain
```
[Contour Feature Extraction]
Extract ordered outer perimeter vertices from 2D view and 3D unfold face.
       │
       ▼
1. Primary: Kabsch Algorithm (SVD) on matched vertex correspondences.
   - Computes optimal rotation R (3x3) and translation t (3x1).
   - If det(R) < 0: handle reflection / sheet flip if allow_mirror=True.
   - Evaluate RMS error over matched points.
       │
       ├─ If RMS <= 0.5 mm ──> SUCCESS (Return Transform T_4x4, T_inv)
       │
       ▼ (if RMS > 0.5 mm or vertex count mismatch)
2. Fallback A: PCA & Bounding Box Principal Axes Alignment.
   - Align centroids and 2 principal inertia axes.
   - Evaluate RMS.
       │
       ├─ If RMS <= 0.5 mm ──> SUCCESS (Return Transform T_4x4, T_inv)
       │
       ▼ (if Fallback A fails)
3. Fallback B: TechDraw View Transform.
   - Direct extraction of Scale, Direction, and Placement from FreeCAD TechDraw::DrawViewPart.
```

### 7.3 Gate Policy
- If RMS $\le 0.5\text{ mm}$: `ok=True`, proceed with alignment.
- If RMS $> 0.5\text{ mm}$: Add warning to `AlignmentResult.warnings`. If `force_alignment=False`, halt and report alignment warning to user in UI; if `force_alignment=True`, proceed with best-fit transform.

---

## 8. § Annotation Targeting (Answers Q7 & Y2)

1. **3D Flat/Bent Annotations:**
   - Anchor coordinates are taken directly from `bend_line_flat_pattern` (midpoint in 3D).
   - Rendered using `App::AnnotationLabel` positioned at $(X_{\text{mid}}, Y_{\text{mid}}, Z_{\max} + 3.0\text{ mm})$.
   - 2D alignment is **not required** for 3D scene annotations.
2. **2D TechDraw Annotations:**
   - Uses the **inverse transformation** $T^{-1}_{4\times4}$ to project 3D anchor points $(X, Y, Z) \to (u, v)$ on the drawing sheet.
   - Attaches `TechDraw::DrawViewAnnotation` or `Balloon` to the 2D sheet.
3. **Idempotence:**
   - All created objects are registered in document group `HandMod_ManualOrder`.
   - Before applying new labels, any existing group members are cleanly removed.

---

## 9. Comprehensive Data Contracts

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class CollisionStage(str, Enum):
    APPROACH = "APPROACH"
    KINEMATICS = "KINEMATICS"
    SELF = "SELF"
    TOOL = "TOOL"
    MACHINE = "MACHINE"
    RETRACT = "RETRACT"
    CONSISTENCY = "consistency"
    BACKGAUGE = "BACKGAUGE"


@dataclass
class RawBendLineSnapshot:
    """Immutable snapshot of raw edge geometry at click time (Decision B2-A)."""
    midpoint: Tuple[float, float, float]
    direction: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    radius_mm: float
    length_mm: float
    adjacent_panel_areas: Tuple[float, float] = (0.0, 0.0)
    source_edge_ref: str = ""


@dataclass
class BendSelection:
    order: int                                     # 1-based order of click (1, 2, 3...)
    edge_ref: str                                  # FreeCAD sub-element name (e.g. "Edge14")
    object_name: str                               # FreeCAD document object name ("Unfold")
    raw_snapshot: RawBendLineSnapshot              # Immutable snapshot of edge geometry
    bend_id: Optional[str] = None                  # Canonical ID resolved in Phase 4 (None at click time)


@dataclass
class ManualSequenceInput:
    fcstd_path: str
    doc_name: str
    part_object_name: str
    unfold_object_name: str
    selections: List[BendSelection]
    tooling_set_id: Optional[str] = None
    punch_id: Optional[str] = None
    die_id: Optional[str] = None
    allow_mirror: bool = True
    max_rms_mm: float = 0.5
    force_alignment: bool = False


@dataclass
class AlignmentResult:
    transform_4x4: List[List[float]]     # 2D -> 3D affine matrix
    transform_inv_4x4: List[List[float]] # 3D -> 2D affine matrix
    rms_error_mm: float
    matched_points: int
    is_mirrored: bool
    method: str                          # "KABSCH", "PCA", "TECHDRAW_NATIVE", "IDENTITY"
    warnings: List[str] = field(default_factory=list)


@dataclass
class CollisionRecord:
    step_order: int
    bend_id: str
    stage: CollisionStage
    message: str
    collision_status: str                # "INVALID" or "UNKNOWN"
    tooling_status: str                  # "REAL" or "SIMPLIFIED"


@dataclass
class OracleStepResult:
    step_order: int
    bend_id: str
    valid: bool
    stage: str
    tool_id: str = "UNKNOWN"
    tooling_status: str = "SIMPLIFIED"
    backgauge: Dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None


@dataclass
class BendAnnotation:
    bend_id: str
    order_label: str                     # "1", "2", "3"...
    anchor_point_3d: Tuple[float, float, float]
    anchor_point_2d: Optional[Tuple[float, float]] = None
    verification_status: str = "CONFIRMED"


@dataclass
class ManualSequenceResult:
    ok: bool
    run_id: str
    run_dir: str
    alignment: AlignmentResult
    steps: List[OracleStepResult]
    collisions: List[CollisionRecord]
    annotations: List[BendAnnotation]
    diagnostics: List[str] = field(default_factory=list)
```
