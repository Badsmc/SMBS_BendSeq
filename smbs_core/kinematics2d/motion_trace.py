"""Read-only per-frame machine-motion trace for PressBrakeSim 2.5D.

JSON Lines captures the exact GUI-rendered sheet geometry, ram and die positions,
FoldState angles, transition endpoints and camera bounds. This module has NO
FreeCAD/Qt dependency and never changes the simulator. Errors during optional
debug recording are reported and may not stop playback.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _rounded(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, (tuple, list)):
        return [_rounded(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _rounded(v) for k, v in value.items()}
    return value


def make_frame_record(*, session, press_cycle, execution_mode, section, pose,
                      pose_warning="", tooling=None, scene_rect=None,
                      dt_s=None, timestamp_s=None, view="2.5D"):
    """Produce a JSON-serializable snapshot from the actual drawn section.

    section is the ALREADY-transformed sheet section, not an independent
    recomputation. The geometry is sampled only for diagnostics, never altered.
    """
    phase = getattr(getattr(press_cycle, "phase", None), "value", "NO_CYCLE")
    status = getattr(getattr(session, "playback_status", None), "value", "UNKNOWN")
    angles = {
        str(k): float(v)
        for k, v in getattr(session.fold_state, "angles", {}).items()
    }
    landmarks = {}
    for key in ("fixed_polygon", "moving_polygon", "zone_polygon"):
        pts = section.get(key, ())
        if pts:
            # The active section is a fixed original reference, so these
            # named points can be compared across bend switches.
            landmarks[key + ":first"] = list(pts[0])
            landmarks[key + ":last"] = list(pts[-1])
    for key in ("other_panel_polygons", "other_zone_polygons"):
        for name, polygon in section.get(key, ()):
            if polygon:
                landmarks[key + ":" + str(name) + ":first"] = list(polygon[0])
    punch = getattr(tooling, "punch_machine_transform", None)
    die = getattr(tooling, "die_machine_transform", None)
    rect = None
    if scene_rect is not None:
        rect = (float(scene_rect.x()), float(scene_rect.y()),
                float(scene_rect.width()), float(scene_rect.height()))
    all_points = [p for key in ("fixed_polygon", "moving_polygon", "zone_polygon")
                  for p in section.get(key, ())]
    for key in ("other_panel_polygons", "other_zone_polygons"):
        all_points += [pt for _id, poly in section.get(key, ()) for pt in poly]
    bbox = None
    if all_points:
        bbox = (
            min(p[0] for p in all_points), min(p[1] for p in all_points),
            max(p[0] for p in all_points), max(p[1] for p in all_points),
        )
    def tf_origin(transform):
        try:
            return transform.apply((0.0, 0.0)) if transform is not None else None
        except (AttributeError, TypeError, ValueError):
            return None
    record = dict(
        kind="FRAME",
        unix_s=float(time.time() if timestamp_s is None else timestamp_s),
        elapsed_dt_s=dt_s,
        view=view,
        active_bend_id=str(session.active_bend_id),
        active_bend_index=int(session.active_bend_index),
        bend_order=list(session.bend_order),
        canonical_order=list(session.canonical_order),
        playback_status=status,
        mode=getattr(execution_mode, "value", str(execution_mode)),
        cycle_phase=phase,
        transfer_progress=float(getattr(press_cycle,"transfer_progress",0.0)),
        transfer_duration_s=float(getattr(press_cycle,"transfer_duration_s",0.0)),
        ram_z_mm=getattr(press_cycle, "ram_z_mm", None),
        ram_stroke_mm=getattr(press_cycle, "ram_stroke_mm", None),
        punch_origin_machine=tf_origin(punch),
        punch_tip_machine=(
            tooling.punch_tip_machine() if tooling is not None else None
        ),
        die_origin_machine=tf_origin(die),
        die_v_center=(tooling.die_v_center_machine()
                      if tooling is not None else None),
        tooling_aligned=bool(getattr(tooling,"aligned",False)),
        angles_deg=angles,
        pose=dict(
            pivot=pose.pivot,
            rotation_deg=pose.rotation_deg,
            mirror_z=pose.mirror_z,
            roll_projection=pose.roll_projection,
            shift=pose.shift,
            mode=pose.mode,
        ),
        pose_warning=str(pose_warning),
        sheet_bbox=bbox,
        # Complete geometric state of every visible panel/bend zone for
        # reproducible, pixel-independent inspection of each animation tick.
        sheet_components={
            key: list(section.get(key, ()))
            for key in ("fixed_polygon","moving_polygon","zone_polygon",
                        "inner_surface","outer_surface","neutral_curve")
        },
        other_panels=list(section.get("other_panel_polygons", ())),
        other_zones=list(section.get("other_zone_polygons", ())),
        landmarks=landmarks,
        scene_rect=rect,
    )
    return _rounded(record)


class MotionTraceRecorder:
    """Buffered JSONL trace with deterministic event/jump summary."""

    def __init__(self, directory, *, jump_mm=2.0, max_frames=200000):
        self.directory = Path(directory).expanduser()
        self.jump_mm = float(jump_mm)
        if not math.isfinite(self.jump_mm) or self.jump_mm <= 0:
            raise ValueError("jump_mm must be positive and finite")
        self.max_frames = int(max_frames)
        self._file = None
        self.path = None
        self.summary_path = None
        self.frames = 0
        self.events = Counter()
        self.warnings = []
        self._previous = None

    @property
    def is_open(self):
        return self._file is not None

    def start(self, label="pressbrake"):
        if self.is_open:
            raise RuntimeError("Trace already started")
        self.directory.mkdir(parents=True,exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = self.directory / ("{}_{}.jsonl".format(label,stamp))
        self.summary_path = self.path.with_suffix(".summary.json")
        self._file = self.path.open("w",encoding="utf-8",buffering=65536)
        self.frames = 0
        self.events.clear()
        self.warnings.clear()
        self._previous = None
        self.event("TRACE_STARTED",path=str(self.path))
        self.flush()
        return self.path

    def flush(self):
        """Persist buffered JSONL frames immediately when requested by GUI."""
        if self._file is not None:
            self._file.flush()

    def _write(self, row):
        if not self.is_open:
            raise RuntimeError("Trace is not running")
        self._file.write(json.dumps(_rounded(row),ensure_ascii=False,
                                    separators=(",",":"),allow_nan=False)+"\n")

    def event(self, name, **fields):
        if not self.is_open:
            return
        self.events[str(name)] += 1
        self._write(dict(kind="EVENT", event=str(name), unix_s=time.time(),
                         frame=self.frames, **fields))

    def record(self, frame):
        if not self.is_open:
            return
        if self.frames >= self.max_frames:
            self.event("MAX_FRAMES_REACHED",max_frames=self.max_frames)
            self.stop("MAX_FRAMES_REACHED")
            return
        prev = self._previous
        current = dict(frame)
        if current.get("kind") != "FRAME":
            raise ValueError("Expected FRAME")
        current["frame"] = self.frames
        if prev is not None:
            for key, ev in (
                ("cycle_phase","PHASE_CHANGE"),
                ("active_bend_id","BEND_SWITCH"),
                ("playback_status","PLAYBACK_STATUS_CHANGE"),
            ):
                if current.get(key)!=prev.get(key):
                    self.event(ev,old=prev.get(key),new=current.get(key))
            old_landmarks = prev.get("landmarks", {})
            new_landmarks = current.get("landmarks", {})
            common = old_landmarks.keys() & new_landmarks.keys()
            if common:
                movement = max(
                    (math.dist(old_landmarks[k],new_landmarks[k]),k)
                    for k in common
                )
                current["max_landmark_step_mm"] = movement[0]
                current["max_landmark_id"] = movement[1]
                dt = current.get("elapsed_dt_s")
                if isinstance(dt,(int,float)) and dt > 0:
                    current["max_landmark_speed_mm_s"] = movement[0]/dt
                # At a bend switch the exact full-sheet frame is required
                # to be continuous. The threshold is intentionally strict.
                bend_changed = (
                    current.get("active_bend_id") != prev.get("active_bend_id")
                )
                if movement[0] > self.jump_mm or (
                    bend_changed and movement[0] > 0.05
                ):
                    # Movement above 2 mm/frame is not necessarily a true
                    # discontinuity: high-speed continuous motion can trigger
                    # this diagnostic. The bend-switch comparison is stricter.
                    self.event("POSITION_JUMP",
                               from_bend=prev.get("active_bend_id"),
                               to_bend=current.get("active_bend_id"),
                               step_mm=round(movement[0],6),
                               landmark=movement[1],
                               previous_phase=prev.get("cycle_phase"),
                               phase=current.get("cycle_phase"),
                               classification=("BEND_SWITCH" if bend_changed
                                               else "FAST_FRAME_MOTION"))
                if bend_changed and movement[0] > 0.05:
                    self.event("BEND_SWITCH_DISCONTINUITY",
                               from_bend=prev.get("active_bend_id"),
                               to_bend=current.get("active_bend_id"),
                               step_mm=round(movement[0],6),
                               landmark=movement[1])
            old_rect, new_rect = prev.get("scene_rect"), current.get("scene_rect")
            if old_rect and new_rect:
                change = max(abs(a-b) for a,b in zip(old_rect,new_rect))
                if change > 0.01:
                    self.event("SCENE_RECT_CHANGED",max_change=round(change,6),
                               old=old_rect,new=new_rect)
            if prev.get("pose_warning") != current.get("pose_warning") and current.get("pose_warning"):
                self.event("POSE_UNSUPPORTED", reason=current["pose_warning"])
        elif current.get("pose_warning"):
            self.event("POSE_UNSUPPORTED", reason=current["pose_warning"])
        self._write(current)
        self.frames += 1
        self._previous = current
        # Flush every second of nominal 40Hz, while keeping UI overhead low.
        if self.frames % 40 == 0:
            self._file.flush()

    def stop(self, reason="STOPPED"):
        if not self.is_open:
            return self.summary_path
        self.event("TRACE_STOPPED",reason=str(reason))
        self._file.flush()
        self._file.close()
        self._file = None
        summary = {
            "schema":"pressbrake-motion-trace-v1",
            "trace_jsonl":str(self.path),
            "frames":self.frames,
            "counts":dict(self.events),
            "jump_threshold_mm":self.jump_mm,
            "finished_reason":str(reason),
            "warning":"Trace describes a kinematic preview, NOT collision certification",
        }
        with self.summary_path.open("w",encoding="utf-8") as output:
            json.dump(summary,output,ensure_ascii=False,indent=2)
        return self.summary_path
