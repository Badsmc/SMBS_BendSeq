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


@dataclass(frozen=True)
class FeedbackSearchOutcome:
    """Bounded planner/verifier loop output; unverified is never approved."""
    verified_sequence: tuple[str, ...] | None
    attempts: int
    blockers: Mapping[tuple[frozenset[str], str, bool], str]
    status: str


def search_with_native_feedback(
    planner,
    native_verifier,
    *,
    max_attempts: int = 5,
) -> FeedbackSearchOutcome:
    """Replan after exact native collisions without discarding other poses.

    `planner(blocked_transitions)` returns ranked candidate objects with
    `.sequence` and `.steps`, or an empty iterable.
    `native_verifier(candidate)` must return either:
      - None: independently verified clear, including all required phases
      - NativeCollisionFeedback: definite native collision of this candidate
      - any other value: unresolved; search stops WITHOUT approval

    The adapter MUST authenticate and validate actual native replay results;
    this function does not perform geometry checks.
    """
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    blockers = {}
    seen = set()
    attempts = 0
    for _ in range(max_attempts):
        candidates = tuple(planner(dict(blockers)))
        new_information = False
        for candidate in candidates:
            sequence = tuple(candidate.sequence)
            orientations = {
                step.bend_id: step.orientation_variant.startswith(
                    "REVERSE_MACHINE_Z_180"
                ) for step in candidate.steps
            }
            identity = (sequence, tuple(sorted(orientations.items())))
            if identity in seen:
                continue
            seen.add(identity)
            attempts += 1
            outcome = native_verifier(candidate)
            if outcome is None:
                return FeedbackSearchOutcome(
                    sequence, attempts, dict(blockers), "NATIVE_VERIFIED"
                )
            if not isinstance(outcome, NativeCollisionFeedback):
                return FeedbackSearchOutcome(
                    None, attempts, dict(blockers), "UNVERIFIED_NATIVE_RESULT"
                )
            if (outcome.sequence != sequence
                    or dict(outcome.orientation_by_bend) != orientations):
                raise ValueError("native replay does not match proposed candidate")
            key, reason = outcome.blocked_transition()
            if key not in blockers:
                blockers[key] = reason
                new_information = True
                break  # Rerun the planner with the NEW exact restriction.
        if not new_information:
            return FeedbackSearchOutcome(
                None, attempts, dict(blockers), "EXHAUSTED_AVAILABLE_CANDIDATES"
            )
    return FeedbackSearchOutcome(
        None, attempts, dict(blockers), "ATTEMPT_LIMIT_REACHED"
    )
