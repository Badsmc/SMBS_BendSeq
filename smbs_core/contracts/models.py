"""
Pydantic Models for SMBS / BendSeq File Contracts.
Mirroring JSON Schemas in smbs_core/contracts/schemas/.
Compliant with ТЗ v1.2.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from smbs_core.protocols.file_store import (
    AlignmentStatus,
    ConfidenceLevel,
    ExportStatus,
    ParameterSource,
    RunStatus,
    ToolingStatus,
    TopologyStatus,
    TransitionStatus,
    VerificationStatus,
)


class ProducerInfo(BaseModel):
    name: str
    version: str


class DiagnosticEntry(BaseModel):
    code: str
    message: str
    level: str = "INFO"  # INFO, WARNING, ERROR
    stage: Optional[str] = None


class ParameterValue(BaseModel):
    value: Optional[Union[float, str]] = None
    unit: Optional[str] = None
    source: ParameterSource = ParameterSource.UNKNOWN
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class GeometryArtifactRef(BaseModel):
    file_path: str
    format: str  # BREP or STEP
    content_hash: str


# ---------------- Canonical Part ----------------

class PanelRecord(BaseModel):
    panel_id: str
    area_mm2: float
    centroid: List[float] = Field(min_length=3, max_length=3)
    normal: List[float] = Field(min_length=3, max_length=3)
    adjacent_bend_ids: List[str] = Field(default_factory=list)


class BendRecord(BaseModel):
    bend_id: str
    parent_panel_id: str
    child_panel_id: str
    angle_deg: ParameterValue
    radius_mm: ParameterValue
    length_mm: ParameterValue
    direction: ParameterValue
    k_factor: ParameterValue
    axis_point: List[float] = Field(min_length=3, max_length=3)
    axis_direction: List[float] = Field(min_length=3, max_length=3)


class CanonicalPartPayload(BaseModel):
    input_mode: str  # NATIVE_SHEETMETAL, NEUTRAL_BREP, CANONICAL_PART
    thickness_mm: ParameterValue
    reference_panel_id: str
    geometry_artifact: GeometryArtifactRef
    panels: Dict[str, PanelRecord]
    bends: Dict[str, BendRecord]


# ---------------- Tooling Manifest ----------------

class PressSpecs(BaseModel):
    press_id: str
    max_tonnage_tons: float
    bed_length_mm: float
    stroke_mm: Optional[float] = None
    open_height_mm: Optional[float] = None


class PunchSpecs(BaseModel):
    tool_id: str
    punch_type: str  # straight, gooseneck, sash
    height_mm: float
    tip_radius_mm: float
    tip_angle_deg: float
    tooling_status: ToolingStatus = ToolingStatus.REAL
    model_path: Optional[str] = None
    model_hash: Optional[str] = None
    segment_layout_mm: List[float] = Field(default_factory=list)


class DieSpecs(BaseModel):
    tool_id: str
    v_opening_mm: float
    v_angle_deg: float
    height_mm: float
    shoulder_radius_mm: Optional[float] = None
    tooling_status: ToolingStatus = ToolingStatus.REAL
    model_path: Optional[str] = None
    model_hash: Optional[str] = None
    segment_layout_mm: List[float] = Field(default_factory=list)


class ToolingManifestPayload(BaseModel):
    press: PressSpecs
    punch: PunchSpecs
    die: DieSpecs
    backgauge: Optional[Dict[str, Any]] = None


# ---------------- Search Request / Result ----------------

class ArtifactRef(BaseModel):
    file_path: str
    content_hash: str


class CostWeights(BaseModel):
    w_tool: float = 100.0
    w_flip: float = 20.0
    w_setup: float = 10.0
    w_time: float = 1.0


class ResourceBudget(BaseModel):
    max_wall_time_s: float = 30.0
    max_nodes: int = 50000
    max_cache_entries: int = 20000
    max_geometry_ops: int = 5000
    max_trajectory_samples: int = 2000
    max_memory_mb: float = 2048.0


class SearchRequestPayload(BaseModel):
    canonical_part_ref: ArtifactRef
    tooling_manifest_ref: ArtifactRef
    search_algorithm: str = "astar"  # astar, dfs, greedy
    candidate_order_policy: str = "outside_in"
    unknown_policy: str = "PRODUCTION"  # PRODUCTION, EXPLORATORY
    cost_weights: CostWeights = Field(default_factory=CostWeights)
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)


class BackgaugePosition(BaseModel):
    x_mm: Optional[float] = None
    r_mm: Optional[float] = None
    z1_mm: Optional[float] = None
    z2_mm: Optional[float] = None
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class CandidateStep(BaseModel):
    step: int
    bend_id: str
    direction: ParameterValue
    angle_deg: ParameterValue
    radius_mm: ParameterValue
    k_factor: Optional[ParameterValue] = None
    backgauge: BackgaugePosition
    tool_id: str
    planner_status: TransitionStatus = TransitionStatus.VALID
    collision_status: TransitionStatus = TransitionStatus.VALID
    tooling_status: ToolingStatus = ToolingStatus.REAL
    alignment_status: AlignmentStatus = AlignmentStatus.OK


class SolverMetrics(BaseModel):
    prefilter_checks: int = 0
    exact_collision_queries: int = 0
    trajectory_samples: int = 0
    solver_nodes: int = 0
    wall_time_s: float = 0.0
    peak_memory_mb: float = 0.0


class SearchResultPayload(BaseModel):
    planning_status: RunStatus
    termination_reason: str
    optimality_claim: str = "best_found_under_budget"
    total_cost: Optional[float] = None
    candidate_sequence: List[CandidateStep] = Field(default_factory=list)
    solver_metrics: SolverMetrics = Field(default_factory=SolverMetrics)


# ---------------- Verification Request / Result ----------------

class ToleranceProfile(BaseModel):
    linear_tol_mm: float = 0.1
    angular_tol_deg: float = 0.5
    volume_tol_mm3: float = 5.0
    relative_volume_tol: float = 0.005
    surface_distance_tol_mm: float = 0.2
    bbox_tol_mm: float = 0.2


class VerificationRequestPayload(BaseModel):
    canonical_part_ref: ArtifactRef
    tooling_manifest_ref: ArtifactRef
    candidate_sequence: List[Dict[str, Any]]
    tolerance_profile: ToleranceProfile = Field(default_factory=ToleranceProfile)


class StepVerificationResult(BaseModel):
    step: int
    bend_id: str
    status: VerificationStatus
    collision_verified: bool = True
    kinematics_verified: bool = True


class IndependentMetrics(BaseModel):
    is_valid_brep: bool
    solids_count: int
    shells_count: int
    volume_delta_mm3: float
    relative_volume_delta: float
    surface_area_delta_mm2: float
    hausdorff_distance_mm: float
    bbox_delta_mm: List[float] = Field(min_length=3, max_length=3)


class VerificationResultPayload(BaseModel):
    verification_status: VerificationStatus
    verification_independence_level: str
    production_eligible: bool
    step_verifications: List[StepVerificationResult]
    independent_metrics: IndependentMetrics


# ---------------- Part BendSeq v1.1 Export ----------------

class ExportSequenceStep(BaseModel):
    step: int
    bend_id: str
    direction: ParameterValue
    angle_deg: ParameterValue
    radius_mm: ParameterValue
    k_factor: ParameterValue
    backgauge: BackgaugePosition
    tool_id: str
    planner_status: TransitionStatus = TransitionStatus.VALID
    collision_status: TransitionStatus = TransitionStatus.VALID
    tooling_status: ToolingStatus = ToolingStatus.REAL
    alignment_status: AlignmentStatus = AlignmentStatus.OK
    verification_status: VerificationStatus = VerificationStatus.VERIFIED


class PartBendSeqExport(BaseModel):
    schema_name: str = "part_bendseq"
    schema_version: str = "1.1"
    run_id: str
    part_id: str
    units: str = "mm"
    input_mode: str
    planning_status: RunStatus
    verification_status: VerificationStatus
    export_status: ExportStatus
    production_eligible: bool
    sequence: List[ExportSequenceStep]
    verification: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    content_hash: str = ""
