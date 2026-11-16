"""Exact-state OCC collision feedback for the experimental axis-staged planner.

Only a confirmed native collision can block a specific transition. AABB
overlap, uncertain outcomes, skipped phases and timeouts are NOT blockers.
An orientation change or a different folded state remains searchable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class NativeCollisionFeedback:
    sequence: tuple[str, ...]
    failing_index: int
    orientation_by_bend: Mapping[str, bool]
    evidence: str
    reason: str

    def blocked_transition(self) -> tuple[tuple[frozenset[str], str, bool], str]:
        """Return an exact transition key suitable for blocked_transitions.

        Requires evidence from authoritative native narrowphase, not a 2D
        projection or broadphase bounding-box risk. Caller must separately
        verify its native result used this sequence and loading.
        """
        if self.evidence != "CAD_3D_INTERFERENCE":
            raise ValueError("only confirmed native CAD collision can block")
        if (not self.sequence or len(self.sequence) != len(set(self.sequence))
                or not 0 <= self.failing_index < len(self.sequence)):
            raise ValueError("invalid verified sequence and collision index")
        if set(self.orientation_by_bend) != set(self.sequence):
            raise ValueError("missing orientation for native replay")
        if any(type(value) is not bool
               for value in self.orientation_by_bend.values()):
            raise ValueError("native replay orientations must be bool")
        bid = self.sequence[self.failing_index]
        return (
            (
                frozenset(self.sequence[:self.failing_index]),
                bid, self.orientation_by_bend[bid],
            ),
            self.reason or "native collision",
        )


def add_native_blocker(
    existing: Mapping[tuple[frozenset[str], str, bool], str],
    feedback: NativeCollisionFeedback,
) -> dict[tuple[frozenset[str], str, bool], str]:
    """Copy-on-write update; prevents false global bend blacklisting."""
    key, reason = feedback.blocked_transition()
    updated = dict(existing)
    updated[key] = reason
    return updated
