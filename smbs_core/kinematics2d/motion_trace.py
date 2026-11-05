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

# The installed FreeCAD add-on declares __version__ independently of the
# s m b s core distribution metadata. It is the project version reported by
# the GUI. Never read Git metadata, as add-ons can be installed without .git.
try:
    from freecad_smbs import __version__ as _PROJECT_VERSION
except ImportError:
    _PROJECT_VERSION = None



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


def extract_physical_landmarks(session, press_cycle) -> dict:
    """Compute exact 3D Machine coordinates [X_m, Y_m, Z_m] of persistent physical points.

    Tracks:
    1. Panel local vertices for each panel in session.panel_vertices_xy.
    2. Bend axis endpoints and midpoint for each bend in session.fold_state.bends.
    All points are mapped through the authoritative 3D workpiece placement in Machine Coordinates.
    """
    landmarks = {}
    if session is None or press_cycle is None:
        return landmarks

    fold_state = getattr(session, "fold_state", None)
    if fold_state is None and getattr(press_cycle, "sequence", None) is not None:
        fold_state = getattr(press_cycle.sequence, "fold_state", None)
    if fold_state is None:
        return landmarks

    placement_fn = getattr(press_cycle, "current_workpiece_placement_3d", None)
    if placement_fn is None or not callable(placement_fn):
        return landmarks

    try:
        placement = placement_fn()
    except Exception:
        return landmarks

    if placement is None:
        return landmarks

    # 1. Panel vertices
    panel_verts = getattr(session, "panel_vertices_xy", None)
    if isinstance(panel_verts, dict):
        transforms = getattr(fold_state, "transforms", {})
        for panel_id, verts in panel_verts.items():
            if panel_id in transforms:
                p_tf = transforms[panel_id]
                for idx, pt in enumerate(verts):
                    try:
                        p_world = p_tf.point((float(pt[0]), float(pt[1]), 0.0))
                        p_machine = placement.apply(p_world)
                        landmarks[f"panel:{panel_id}:v{idx}"] = [
                            round(p_machine[0], 6),
                            round(p_machine[1], 6),
                            round(p_machine[2], 6),
                        ]
                    except Exception:
                        pass

    # 2. Bend axes (start, end, mid)
    bends = getattr(fold_state, "bends", None)
    if isinstance(bends, dict):
        transforms = getattr(fold_state, "transforms", {})
        for bend_id, bend in bends.items():
            try:
                fixed_panel = getattr(bend, "fixed_panel", None)
                p_tf = transforms.get(fixed_panel)
                axis_xyz = getattr(bend, "axis_xyz", None)
                if p_tf is not None and axis_xyz is not None and len(axis_xyz) == 2:
                    p0_loc, p1_loc = axis_xyz
                    p0_w = p_tf.point(p0_loc)
                    p1_w = p_tf.point(p1_loc)
                    p0_m = placement.apply(p0_w)
                    p1_m = placement.apply(p1_w)
                    mid_m = (
                        0.5 * (p0_m[0] + p1_m[0]),
                        0.5 * (p0_m[1] + p1_m[1]),
                        0.5 * (p0_m[2] + p1_m[2]),
                    )
                    landmarks[f"bend:{bend_id}:start"] = [round(p0_m[0], 6), round(p0_m[1], 6), round(p0_m[2], 6)]
                    landmarks[f"bend:{bend_id}:end"] = [round(p1_m[0], 6), round(p1_m[1], 6), round(p1_m[2], 6)]
                    landmarks[f"bend:{bend_id}:mid"] = [round(mid_m[0], 6), round(mid_m[1], 6), round(mid_m[2], 6)]
            except Exception:
                pass

    return landmarks


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

    coupled = {}
    if hasattr(press_cycle, "coupled_telemetry") and callable(getattr(press_cycle, "coupled_telemetry")):
        try:
            coupled = press_cycle.coupled_telemetry() or {}
        except Exception:
            coupled = {}

    punch_stroke = coupled.get("punch_stroke_mm", getattr(press_cycle, "ram_stroke_mm", None))
    bend_angle = coupled.get("bend_angle_deg", angles.get(str(session.active_bend_id)))
    contact_pts = coupled.get("contact_points")
    bend_zone_pos = coupled.get("bend_zone_position")
    punch_clearance = coupled.get("punch_clearance_mm")
    validation_status = coupled.get("validation_status", "UNVERIFIED: no coupled kinematics")
    tooling_status = coupled.get("tooling_status", "NOT_SPECIFIED")
    collision_validated = coupled.get("collision_validated", False)
    support_validated = coupled.get("support_validated", False)

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
        adjacent_panels=(
            [
                getattr(getattr(getattr(session, "fold_state", None), "bends", {}).get(str(session.active_bend_id)), "fixed_panel", None),
                getattr(getattr(getattr(session, "fold_state", None), "bends", {}).get(str(session.active_bend_id)), "moving_panel", None),
            ]
            if getattr(session, "fold_state", None) is not None
            else None
        ),
        transfer_progress=float(getattr(press_cycle,"transfer_progress",0.0)),
        transfer_duration_s=float(getattr(press_cycle,"transfer_duration_s",0.0)),
        ram_z_mm=getattr(press_cycle, "ram_z_mm", None),
        ram_stroke_mm=getattr(press_cycle, "ram_stroke_mm", None),
        punch_stroke_mm=punch_stroke,
        bend_angle_deg=bend_angle,
        contact_points=contact_pts,
        bend_zone_position=bend_zone_pos,
        punch_clearance_mm=punch_clearance,
        validation_status=str(validation_status),
        tooling_status=str(tooling_status),
        collision_validated=bool(collision_validated),
        support_validated=bool(support_validated),
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
        physical_landmarks=extract_physical_landmarks(session, press_cycle),
        scene_rect=rect,
    )
    return _rounded(record)


class MotionTraceRecorder:
    """Buffered JSONL trace with deterministic event/jump summary."""

    def __init__(self, directory, *, jump_mm=2.0, max_frames=200000,
                 project_version=None, build_id=None, project_channel="2D"):
        self.directory = Path(directory).expanduser()
        self.project_version = str(project_version or _PROJECT_VERSION or "UNKNOWN")
        self.project_channel = str(project_channel)
        self.build_id = str(build_id or "UNKNOWN")
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
        self.event("TRACE_STARTED", path=str(self.path),
                   project_version=self.project_version,
                   project_channel=self.project_channel,
                   build_id=self.build_id)
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
            bend_changed = (
                current.get("active_bend_id") != prev.get("active_bend_id")
            )
            curr_phase = current.get("cycle_phase")
            prev_phase = prev.get("cycle_phase")
            dt = current.get("elapsed_dt_s")

            prev_phys = prev.get("physical_landmarks") or {}
            curr_phys = current.get("physical_landmarks") or {}
            common_phys = prev_phys.keys() & curr_phys.keys()

            if common_phys:
                phys_movement = max(
                    (math.dist(prev_phys[k], curr_phys[k]), k)
                    for k in common_phys
                )
                step_mm = phys_movement[0]
                landmark_id = phys_movement[1]
                current["max_physical_step_mm"] = step_mm
                current["max_physical_landmark_id"] = landmark_id
                speed_mm_s = 0.0
                if isinstance(dt, (int, float)) and dt > 0:
                    speed_mm_s = step_mm / dt
                    current["max_physical_speed_mm_s"] = speed_mm_s

                is_discontinuity = False
                discontinuity_reason = ""

                # 1. Bend switch continuity
                # A bend switch occurs across a continuous motion phase (typically TRANSFER -> APPROACH).
                # The allowable step is governed by the allowable motion velocity across the frame dt,
                # while a stationary or frozen switch is held to tolerance switch_tol = 0.05 mm.
                if bend_changed:
                    allowed_switch_step = max(0.05, 500.0 * (dt or 0.05)) if (prev_phase == "TRANSFER" or curr_phase == "TRANSFER") else 0.05
                    if step_mm > allowed_switch_step:
                        is_discontinuity = True
                        discontinuity_reason = (
                            f"BEND_SWITCH_DISCONTINUITY: physical step {step_mm:.4f}mm > {allowed_switch_step:.2f}mm "
                            f"at {prev.get('active_bend_id')}->{current.get('active_bend_id')}"
                        )
                        self.event("BEND_SWITCH_DISCONTINUITY",
                                   from_bend=prev.get("active_bend_id"),
                                   to_bend=current.get("active_bend_id"),
                                   step_mm=round(step_mm, 6),
                                   landmark=landmark_id)
                    else:
                        self.event("BEND_SWITCH_CONTINUOUS",
                                   from_bend=prev.get("active_bend_id"),
                                   to_bend=current.get("active_bend_id"),
                                   step_mm=round(step_mm, 6),
                                   landmark=landmark_id)

                # 2. Phase-aware velocity envelope
                prev_phase = prev.get("cycle_phase")
                phase_changed = (prev_phase != curr_phase)
                if not is_discontinuity:
                    if curr_phase in ("IDLE", "BOTTOM", "CONTACT", "STEP_COMPLETE"):
                        if phase_changed and prev_phase in ("BENDING", "RETRACT"):
                            # Normal phase boundary step from preceding movement
                            allowed_step = max(0.5, 500.0 * (dt or 0.05))
                            if step_mm > allowed_step:
                                is_discontinuity = True
                                discontinuity_reason = f"PHASE_TRANSITION_STEP_EXCEEDED: step {step_mm:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{curr_phase}"
                        else:
                            if step_mm > 0.05:
                                is_discontinuity = True
                                discontinuity_reason = f"STATIONARY_PHASE_MOTION: step {step_mm:.4f}mm in {curr_phase}"
                    elif curr_phase in ("APPROACH", "RETRACT"):
                        if phase_changed and prev_phase in ("TRANSFER", "BOTTOM"):
                            allowed_step = max(0.5, 500.0 * (dt or 0.05))
                            if step_mm > allowed_step:
                                is_discontinuity = True
                                discontinuity_reason = f"PHASE_TRANSITION_STEP_EXCEEDED: step {step_mm:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{curr_phase}"
                        else:
                            if step_mm > 0.05:
                                is_discontinuity = True
                                discontinuity_reason = f"WORKPIECE_MOTION_DURING_RAM_TRAVEL: step {step_mm:.4f}mm in {curr_phase}"
                    elif curr_phase == "BENDING":
                        allowed_step = max(0.5, 500.0 * (dt or 0.05))
                        if step_mm > allowed_step:
                            is_discontinuity = True
                            discontinuity_reason = f"BENDING_STEP_EXCEEDED: step {step_mm:.4f}mm > {allowed_step:.2f}mm"
                    elif curr_phase == "TRANSFER":
                        allowed_step = max(1.0, 1200.0 * (dt or 0.05))
                        if step_mm > allowed_step:
                            is_discontinuity = True
                            discontinuity_reason = f"TRANSFER_STEP_EXCEEDED: step {step_mm:.4f}mm > {allowed_step:.2f}mm"

                if is_discontinuity:
                    self.event("PHYSICAL_DISCONTINUITY",
                               phase=curr_phase,
                               step_mm=round(step_mm, 6),
                               landmark=landmark_id,
                               speed_mm_s=round(speed_mm_s, 2),
                               reason=discontinuity_reason)

                # 2D section slice polygon update is classified as SECTION_RECONSTRUCTION
                old_sec = prev.get("landmarks", {})
                new_sec = current.get("landmarks", {})
                common_sec = old_sec.keys() & new_sec.keys()
                if common_sec:
                    sec_movement = max(
                        (math.dist(old_sec[k], new_sec[k]), k)
                        for k in common_sec
                    )
                    current["max_landmark_step_mm"] = sec_movement[0]
                    current["max_landmark_id"] = sec_movement[1]
                    if isinstance(dt, (int, float)) and dt > 0:
                        current["max_landmark_speed_mm_s"] = sec_movement[0] / dt
                    if bend_changed and sec_movement[0] > 0.05:
                        self.event("SECTION_RECONSTRUCTION",
                                   from_bend=prev.get("active_bend_id"),
                                   to_bend=current.get("active_bend_id"),
                                   step_mm=round(sec_movement[0], 6),
                                   landmark=sec_movement[1])
            else:
                old_landmarks = prev.get("landmarks", {})
                new_landmarks = current.get("landmarks", {})
                common = old_landmarks.keys() & new_landmarks.keys()
                if common:
                    movement = max(
                        (math.dist(old_landmarks[k], new_landmarks[k]), k)
                        for k in common
                    )
                    current["max_landmark_step_mm"] = movement[0]
                    current["max_landmark_id"] = movement[1]
                    dt = current.get("elapsed_dt_s")
                    if isinstance(dt, (int, float)) and dt > 0:
                        current["max_landmark_speed_mm_s"] = movement[0] / dt
                    if movement[0] > self.jump_mm or (
                        bend_changed and movement[0] > 0.05
                    ):
                        self.event("POSITION_JUMP",
                                   from_bend=prev.get("active_bend_id"),
                                   to_bend=current.get("active_bend_id"),
                                   step_mm=round(movement[0], 6),
                                   landmark=movement[1],
                                   previous_phase=prev.get("cycle_phase"),
                                   phase=current.get("cycle_phase"),
                                   classification=("BEND_SWITCH" if bend_changed
                                                   else "FAST_FRAME_MOTION"))
                    if bend_changed and movement[0] > 0.05:
                        self.event("BEND_SWITCH_DISCONTINUITY",
                                   from_bend=prev.get("active_bend_id"),
                                   to_bend=current.get("active_bend_id"),
                                   step_mm=round(movement[0], 6),
                                   landmark=movement[1])

            old_rect, new_rect = prev.get("scene_rect"), current.get("scene_rect")
            if old_rect and new_rect:
                change = max(abs(a-b) for a,b in zip(old_rect, new_rect))
                if change > 0.01:
                    self.event("SCENE_RECT_CHANGED", max_change=round(change, 6),
                               old=old_rect, new=new_rect)
            if prev.get("pose_warning") != current.get("pose_warning") and current.get("pose_warning"):
                self.event("POSE_UNSUPPORTED", reason=current["pose_warning"])
        elif current.get("pose_warning"):
            self.event("POSE_UNSUPPORTED", reason=current["pose_warning"])

        # Tasks 7 & 8: Validate punch contact & die support during BENDING and BOTTOM
        curr_phase = current.get("cycle_phase")
        if curr_phase in ("BENDING", "BOTTOM"):
            clearance = current.get("punch_clearance_mm")
            ram_z = current.get("ram_z_mm")
            b_zone = current.get("bend_zone_position")
            contact_ok = True
            if clearance is not None and clearance > 0.1:
                contact_ok = False
                self.event("PUNCH_CONTACT_LOSS",
                           phase=curr_phase,
                           clearance_mm=clearance,
                           frame=self.frames)
            elif ram_z is not None and b_zone and b_zone.get("inner_apex"):
                inner_z = b_zone["inner_apex"][1]
                if ram_z < inner_z - 0.1:
                    contact_ok = False
                    self.event("PUNCH_PENETRATION_VIOLATION",
                               phase=curr_phase,
                               penetration_mm=round(inner_z - ram_z, 4),
                               frame=self.frames)
            current["punch_contact_valid"] = contact_ok

            if curr_phase == "BENDING":
                val_status = current.get("validation_status", "")
                if "UNSUPPORTED" in val_status:
                    self.event("DIE_SUPPORT_VIOLATION",
                               phase=curr_phase,
                               status=val_status,
                               frame=self.frames)
        else:
            current["punch_contact_valid"] = None

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
        phys_disc = self.events["PHYSICAL_DISCONTINUITY"] + self.events["BEND_SWITCH_DISCONTINUITY"]
        contact_viols = self.events["PUNCH_CONTACT_LOSS"] + self.events["PUNCH_PENETRATION_VIOLATION"]
        die_viols = self.events["DIE_SUPPORT_VIOLATION"]
        summary = {
            "schema":"pressbrake-motion-trace-v1",
            "project_version":self.project_version,
            "project_channel":self.project_channel,
            "build_id":self.build_id,
            "trace_jsonl":str(self.path),
            "frames":self.frames,
            "counts":dict(self.events),
            "jump_threshold_mm":self.jump_mm,
            "finished_reason":str(reason),
            "physical_continuity_status": "PASS" if phys_disc == 0 else "FAIL",
            "geometric_consistency_status": "PASS" if (contact_viols + die_viols) == 0 else "FAIL",
            "warning":"Trace describes a kinematic preview, NOT collision certification",
        }
        with self.summary_path.open("w",encoding="utf-8") as output:
            json.dump(summary,output,ensure_ascii=False,indent=2)
        return self.summary_path
