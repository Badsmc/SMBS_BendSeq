from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

@dataclass
class BendRecordModel:
    bend_id: str
    parent_panel_id: str
    child_panel_id: str
    axis_origin: Tuple[float, float, float]
    axis_direction: Tuple[float, float, float]
    signed_angle: float
    radius: float
    length: float

@dataclass
class PartModel:
    model_id: str
    thickness: float
    material: str
    bends: Dict[str, BendRecordModel] = field(default_factory=dict)
    panels: List[str] = field(default_factory=list)

@dataclass
class StepValidation:
    bend_id: str
    status: str  # VALID, INVALID, UNKNOWN
    rejection_reason: Optional[str] = None
    rejection_stage: Optional[str] = None

@dataclass
class ValidationReport:
    schema_version: str = "1.0"
    success: bool = False
    error: Optional[str] = None
    steps: List[StepValidation] = field(default_factory=list)

@dataclass
class BendPlan:
    schema_version: str = "1.0"
    model_id: str = ""
    sequence: List[Dict] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    validation: Optional[ValidationReport] = None
