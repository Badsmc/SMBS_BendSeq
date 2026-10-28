"""
fc_animation.py - Headless animation controller for FreeCAD Timeline UI (Req 2 Phase F.2).

Pure Python (no FreeCAD, no Qt imports). FreeCAD macro `SMBS_AnimationPlayer.FCMacro`
wraps this controller with QDialog + QTimer.

Design:
- AnimationController: play/pause/tick state machine over Player.
  Timer advancement is external (`tick()` called by QTimer or by tests).
- Badge helpers: verification_status -> (text, rgb) for honest UI marking.
- precision banner: geometry_precision -> (text, rgb).
"""
from __future__ import annotations

from typing import Optional, Tuple

from smbs_core.viewer.player import Frame, Player
from smbs_core.viewer.replayer import (
    GEOMETRY_PRECISION_VALID,
    GEOMETRY_PRECISION_PREVIEW,
)


COLOR_GREEN = (0.0, 0.6, 0.0)
COLOR_ORANGE = (1.0, 0.5, 0.0)
COLOR_YELLOW = (0.9, 0.7, 0.0)
COLOR_GRAY = (0.5, 0.5, 0.5)


class AnimationController:
    """Playback state over a Player. Owns nothing except the playing flag.

    Lifecycle:
      controller = AnimationController(player)
      controller.reset()        # position 0, paused
      controller.play()         # playing = True
      while playing: controller.tick()  # advance one frame, or None at end
      controller.pause()        # playing = False
    """

    def __init__(self, player: Player) -> None:
        self._player = player
        self._playing: bool = False

    @property
    def player(self) -> Player:
        return self._player

    def is_playing(self) -> bool:
        return self._playing

    def current(self) -> Optional[Frame]:
        return self._player.current()

    def reset(self) -> Frame:
        self._playing = False
        return self._player.reset()

    def play(self) -> None:
        # If at end, restart from step 0 (user intent: play from anywhere)
        cur = self._player.current()
        if cur is not None and cur.frame_index == self._player.total - 1:
            self._player.reset()
        elif cur is None:
            self._player.reset()
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def tick(self) -> Optional[Frame]:
        """Advance one frame if playing.

        Returns:
          - new Frame if advanced;
          - None if not advanced (either paused, or end-of-sequence reached).

        If end-of-sequence reached while playing, playing stops automatically
        (caller's QTimer should stop; UI still renders last frame via current()).
        """
        if not self._playing:
            return None
        nxt = self._player.next()
        if nxt is None:
            self._playing = False
            return None
        return nxt

    def step_forward(self) -> Optional[Frame]:
        """Manual single-step forward. Pauses if playing."""
        self._playing = False
        return self._player.next()

    def step_backward(self) -> Optional[Frame]:
        """Manual single-step backward. Pauses if playing."""
        self._playing = False
        return self._player.prev()


def format_verification_badge(frame: Frame) -> Tuple[str, Tuple[float, float, float]]:
    """verification_status -> (text, rgb). Honest marking, no computation."""
    v = frame.verification_status
    if v == "VERIFIED":
        return ("VERIFIED", COLOR_GREEN)
    if v == "VERIFICATION_FAILED":
        return ("FAILED", COLOR_ORANGE)
    return ("UNVERIFIED", COLOR_GRAY)


def format_precision_badge(frame: Frame) -> Tuple[str, Tuple[float, float, float]]:
    """geometry_precision -> (text, rgb). VALID gets green, PREVIEW gets yellow."""
    p = frame.step_view.geometry_precision
    if p == GEOMETRY_PRECISION_VALID:
        return ("VALID_PER_PANEL", COLOR_GREEN)
    if p == GEOMETRY_PRECISION_PREVIEW:
        return ("PREVIEW_ONLY", COLOR_YELLOW)
    return ("UNKNOWN", COLOR_GRAY)


def format_machine_frame_badge(machine_frame_present: bool) -> Tuple[str, Tuple[float, float, float]]:
    """machine_frame availability -> (text, rgb). F.3a: always SIMPLIFIED."""
    if machine_frame_present:
        return ("MACHINE: LOADED", COLOR_GREEN)
    return ("MACHINE: SIMPLIFIED (KL-16 pending)", COLOR_YELLOW)


def format_tooling_badge(punch_present: bool, die_present: bool) -> Tuple[str, Tuple[float, float, float]]:
    """punch/die visual availability -> (text, rgb)."""
    if punch_present and die_present:
        return ("TOOLING: PUNCH + DIE", COLOR_GREEN)
    if punch_present or die_present:
        return ("TOOLING: PARTIAL", COLOR_ORANGE)
    return ("TOOLING: NONE", COLOR_GRAY)


def format_step_header(frame: Frame, direction: str = "backward") -> str:
    """Human-readable header line for dialog top."""
    sv = frame.step_view
    if direction == "forward":
        if frame.frame_index == 0:
            return "Step 0 — flat pattern"
        parts = [f"Step {frame.frame_index}"]
        if sv.bend_id:
            parts.append(sv.bend_id)
        if sv.ui_direction:
            parts.append(f"({sv.ui_direction})")
        return " — ".join(parts)

    if sv.step_index == 0:
        return "Step 0 — initial (fully folded)"
    parts = [f"Step {sv.step_index}"]
    if sv.bend_id:
        parts.append(sv.bend_id)
    if sv.ui_direction:
        parts.append(f"({sv.ui_direction})")
    return " — ".join(parts)
