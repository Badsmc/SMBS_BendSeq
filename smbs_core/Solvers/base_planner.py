"""
base_planner.py - Base Interface for Backward Bend Sequence Planners (SMBS Phase 8)
"""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from smbs_core.Core.fold_state import FoldState
from smbs_core.Physics.validator import PhysicalValidator


class PlannerStatus(Enum):
    FOUND = "FOUND"
    BEST_FOUND = "BEST_FOUND"
    OPTIMAL = "OPTIMAL"
    NOT_FOUND_BUDGET = "NOT_FOUND_BUDGET"
    NOT_FOUND_ALL_REJECTED = "NOT_FOUND_ALL_REJECTED"
    CANNOT_COMPUTE = "CANNOT_COMPUTE"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value == other.upper()
        return super().__eq__(other)


@dataclass
class PlanResult:
    status: PlannerStatus
    success: bool
    sequence: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[str] = field(default_factory=list)


class BasePlanner(ABC):
    """
    Abstract Base Class for sequence search planners operating on FoldState & Physical Oracle.
    """

    def __init__(self, validator: PhysicalValidator):
        self.validator = validator

    @abstractmethod
    def solve(self, initial_state: FoldState, max_iterations: int = 5000) -> Dict[str, Any]:
        """
        Execute search algorithm to find a verified collision-free fold sequence.

        :param initial_state: Root FoldState.
        :param max_iterations: Maximum node expansions allowed.
        :return: Planning result dictionary.
        """
        pass

    def compute_heuristic(self, state: FoldState) -> float:
        """
        Domain heuristic estimate for remaining unfolding cost.
        Returns number of remaining folded bends.
        """
        num_remaining = len(state.remaining_bends)
        if num_remaining == 0:
            return 0.0
        return float(num_remaining)
