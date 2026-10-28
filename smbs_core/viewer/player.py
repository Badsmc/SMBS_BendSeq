"""
player.py - State-machine timeline player for viewer (Stage 4, S.14).

Reads 09_verification_result.json as the sole source of production_eligible
(invariant #9). Never computes it. Absence of 09 => verification_status="UNVERIFIED"
(invariant #1: UNKNOWN != VALID). Forward mode implemented via honest reversal of backward StepViews (S.33, Req 2 F.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from smbs_core.viewer.graph_builder import ViewerGraphs
from smbs_core.viewer.replayer import (
    GEOMETRY_PRECISION_PREVIEW,
    StepView,
    replay_backward,
)
from smbs_core.viewer.run_loader import RunArtifacts


VERIFICATION_UNVERIFIED = "UNVERIFIED"  # viewer-side marker; not a schema enum


class PlayerError(Exception):
    """Raised when timeline playback fails or invalid player action requested."""


@dataclass(frozen=True)
class Frame:
    frame_index: int                     # 0..N
    step_view: StepView
    verification_status: str             # "VERIFIED" | "VERIFICATION_FAILED" | "UNVERIFIED"
    production_eligible: Optional[bool]  # None if 09 absent
    diagnostics: Tuple[Any, ...]         # top-level 09 "diagnostics"; () if 09 absent
    step_verification: Optional[Dict[str, Any]]  # matching payload.step_verifications[i] by bend_id; None otherwise


class Player:
    def __init__(
        self,
        artifacts: RunArtifacts,
        graphs: ViewerGraphs,
        backend: Any,
        initial_shape: Optional[Any] = None
    ):
        self.artifacts = artifacts
        self.graphs = graphs
        self.backend = backend
        self.initial_shape = initial_shape

        self._cached_frames: Optional[Tuple[Frame, ...]] = None
        self._cached_forward_frames: Optional[Tuple[Frame, ...]] = None
        self._current_index: Optional[int] = None
        self._direction: str = "backward"

    @property
    def direction(self) -> str:
        return self._direction

    @property
    def frames(self) -> Tuple[Frame, ...]:
        if self._direction == "forward":
            if self._cached_forward_frames is None:
                self._cached_forward_frames = self._build_forward_frames()
            return self._cached_forward_frames
        if self._cached_frames is None:
            self._cached_frames = self._build_backward_frames()
        return self._cached_frames

    def _build_backward_frames(self) -> Tuple[Frame, ...]:
        # 1. Resolve verification fields strictly without calculation
        v_res = self.artifacts.verification_result
        if v_res is None:
            v_status = VERIFICATION_UNVERIFIED
            prod_elig = None
            diags: Tuple[Any, ...] = ()
            step_verifs_by_bend: Dict[str, Dict[str, Any]] = {}
        else:
            try:
                payload = v_res["payload"]
                v_status = payload["verification_status"]
                prod_elig = payload["production_eligible"]
            except (KeyError, TypeError) as err:
                raise PlayerError(f"09 missing required payload field: {err}") from err

            try:
                raw_diags = v_res["diagnostics"]
            except (KeyError, TypeError) as err:
                raise PlayerError(f"09 missing top-level 'diagnostics': {err}") from err

            if not isinstance(raw_diags, list):
                raise PlayerError(f"09 'diagnostics' must be a list, got {type(raw_diags)}")
            diags = tuple(raw_diags)

            try:
                raw_step_verifs = payload["step_verifications"]
            except (KeyError, TypeError) as err:
                raise PlayerError(f"09 payload missing 'step_verifications': {err}") from err

            if not isinstance(raw_step_verifs, list):
                raise PlayerError(f"09 'step_verifications' must be a list, got {type(raw_step_verifs)}")

            step_verifs_by_bend = {}
            for sv in raw_step_verifs:
                if isinstance(sv, dict) and "bend_id" in sv:
                    step_verifs_by_bend[sv["bend_id"]] = sv

        # 2. Generate StepViews via replayer
        step_views = list(
            replay_backward(
                artifacts=self.artifacts,
                graphs=self.graphs,
                backend=self.backend,
                initial_shape=self.initial_shape
            )
        )

        frames_list = []
        for idx, sv in enumerate(step_views):
            step_v = None
            if sv.bend_id is not None:
                step_v = step_verifs_by_bend.get(sv.bend_id)

            frames_list.append(
                Frame(
                    frame_index=idx,
                    step_view=sv,
                    verification_status=v_status,
                    production_eligible=prod_elig,  # Invariant #9: Never computed; taken as-is
                    diagnostics=diags,
                    step_verification=step_v
                )
            )

        return tuple(frames_list)

    def _build_forward_frames(self) -> Tuple[Frame, ...]:
        # Forward = honest reversal of backward StepViews. Verification fields
        # are keyed by bend_id (direction-invariant), so reversal preserves
        # honest marking.
        if self._cached_frames is None:
            self._cached_frames = self._build_backward_frames()
        return tuple(
            Frame(
                frame_index=i,
                step_view=f.step_view,
                verification_status=f.verification_status,
                production_eligible=f.production_eligible,
                diagnostics=f.diagnostics,
                step_verification=f.step_verification,
            )
            for i, f in enumerate(reversed(self._cached_frames))
        )

    @property
    def total(self) -> int:
        return len(self.frames)

    def reset(self) -> Frame:
        """Reset player to initial frame (index 0)."""
        f = self.frames
        self._current_index = 0
        return f[0]

    def current(self) -> Optional[Frame]:
        """Returns current frame or None before first navigation/reset."""
        if self._current_index is None:
            return None
        return self.frames[self._current_index]

    def next(self) -> Optional[Frame]:
        """Advance to next frame. Returns None at the end without changing index."""
        if self._current_index is None:
            self._current_index = 0
            return self.frames[0]

        if self._current_index + 1 < self.total:
            self._current_index += 1
            return self.frames[self._current_index]
        return None

    def prev(self) -> Optional[Frame]:
        """Move to previous frame. Returns None at beginning without changing index."""
        if self._current_index is None:
            return None

        if self._current_index > 0:
            self._current_index -= 1
            return self.frames[self._current_index]
        return None

    def switch_direction(self, direction: str) -> None:
        """Switch playback direction: 'backward' (unfold) or 'forward' (fold).
        Forward mode is honest reversal of backward StepViews — same kinematics,
        same verification fields (looked up by bend_id, direction-invariant).
        Current position is reset on direction change."""
        if direction == "forward":
            self._direction = "forward"
        elif direction == "backward":
            self._direction = "backward"
        else:
            raise PlayerError(f"Unsupported playback direction: '{direction}'")
        self._current_index = None
