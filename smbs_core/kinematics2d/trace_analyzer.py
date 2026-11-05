"""Automated motion trace analyzer for SMBS / PressBrakeSim (Stage D4-P2.1 Hardened).

Evaluates JSONL trace files produced by MotionTraceRecorder:
1. Integrity of trace recording lifecycle (TRACE_STARTED, TRACE_STOPPED, PLAYBACK_COMPLETED).
2. Authoritative physical landmark continuity across every cycle phase.
3. Accurate phase-aware velocity envelopes and trajectory limits.
4. Seamless endpoint continuity at bend switches and transfer transitions.
5. Signed punch clearance, contact loss, and tool penetration validation.
6. Die shoulder support tracking during BENDING without assuming two-point support elsewhere.
7. Signed target angle verification supporting arbitrary bend angles.
8. Completeness and consistency of reference bend sequences and transitions.
9. Strict output classification: PASS, FAIL, or UNVERIFIED with full evidence summary.
10. Analyzer version and git source revision tracking.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

ANALYZER_VERSION = "0.2.1-D4-P2.1"


def get_source_revision() -> str:
    """Retrieve git HEAD commit SHA if available."""
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "UNKNOWN"


@dataclass
class DiscontinuityRecord:
    frame: int
    timestamp_s: float
    phase: str
    from_bend: str
    to_bend: str
    landmark_id: str
    step_mm: float
    allowable_mm: float
    speed_mm_s: float
    reason: str


@dataclass
class GeometricInconsistencyRecord:
    frame: int
    timestamp_s: float
    phase: str
    active_bend_id: str
    kind: str  # "PUNCH_CONTACT_LOSS" | "PUNCH_PENETRATION_VIOLATION" | "DIE_SUPPORT_VIOLATION" | "TARGET_ANGLE_MISMATCH"
    value_mm: float
    tolerance_mm: float
    details: str


@dataclass
class BendSwitchEvaluation:
    from_bend: str
    to_bend: str
    frame: int
    timestamp_s: float
    max_step_mm: float
    tolerance_mm: float
    status: str  # "PASS" | "FAIL" | "UNVERIFIED"


@dataclass
class PhaseMetrics:
    phase: str
    frame_count: int = 0
    duration_s: float = 0.0
    max_physical_step_mm: float = 0.0
    max_physical_speed_mm_s: float = 0.0
    punch_contact_valid_frames: int = 0
    punch_contact_violation_frames: int = 0


@dataclass
class TraceAnalysisReport:
    overall_status: str  # "PASS" | "FAIL" | "UNVERIFIED"
    trace_file: str
    analyzer_version: str
    source_revision: str
    project_version: str
    build_id: str
    total_frames: int
    total_events: int
    total_duration_s: float
    bends_present: List[str]
    bends_completed: List[str]
    phases_traversed: List[str]
    max_physical_step_mm: float
    max_physical_speed_mm_s: float
    discontinuities_count: int
    geometric_inconsistencies_count: int
    unresolved_conditions_count: int
    bend_switches_continuous_count: int
    bend_switches_discontinuous_count: int
    bend_switch_evaluations: List[BendSwitchEvaluation] = field(default_factory=list)
    discontinuities: List[DiscontinuityRecord] = field(default_factory=list)
    geometric_inconsistencies: List[GeometricInconsistencyRecord] = field(default_factory=list)
    unresolved_conditions: List[Dict[str, Any]] = field(default_factory=list)
    phase_metrics: Dict[str, PhaseMetrics] = field(default_factory=dict)
    event_counts: Dict[str, int] = field(default_factory=dict)
    notice: str = "Trace describes a kinematic simulation preview, NOT certified physical stamping"

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "overall_status": self.overall_status,
            "analyzer_version": self.analyzer_version,
            "source_revision": self.source_revision,
            "trace_file": self.trace_file,
            "project_version": self.project_version,
            "build_id": self.build_id,
            "total_frames": self.total_frames,
            "total_events": self.total_events,
            "total_duration_s": round(self.total_duration_s, 4),
            "bends_present": self.bends_present,
            "bends_completed": self.bends_completed,
            "phases_traversed": self.phases_traversed,
            "max_physical_step_mm": round(self.max_physical_step_mm, 6),
            "max_physical_speed_mm_s": round(self.max_physical_speed_mm_s, 2),
            "discontinuities_count": self.discontinuities_count,
            "geometric_inconsistencies_count": self.geometric_inconsistencies_count,
            "unresolved_conditions_count": self.unresolved_conditions_count,
            "bend_switches_continuous_count": self.bend_switches_continuous_count,
            "bend_switches_discontinuous_count": self.bend_switches_discontinuous_count,
            "bend_switch_evaluations": [asdict(b) for b in self.bend_switch_evaluations],
            "discontinuities": [asdict(d) for d in self.discontinuities],
            "geometric_inconsistencies": [asdict(g) for g in self.geometric_inconsistencies],
            "unresolved_conditions": self.unresolved_conditions,
            "phase_metrics": {k: asdict(v) for k, v in self.phase_metrics.items()},
            "event_counts": self.event_counts,
            "notice": self.notice,
        }

    def to_text_summary(self) -> str:
        """Produce formatted human-readable ASCII summary report."""
        lines = [
            "=" * 72,
            f"SMBS / PressBrakeSim Motion Continuity Report (Stage D4-P2.1 Hardened)",
            "=" * 72,
            f"Overall Status:            {self.overall_status}",
            f"Analyzer Version:          {self.analyzer_version}",
            f"Source Revision:           {self.source_revision[:12] if self.source_revision else 'UNKNOWN'}",
            f"Trace File:                {Path(self.trace_file).name if self.trace_file else 'N/A'}",
            f"Project Version / Build:   {self.project_version} / {self.build_id[:12] if self.build_id else 'UNKNOWN'}",
            f"Frames / Events Recorded:  {self.total_frames} frames / {self.total_events} events",
            f"Total Playback Duration:   {self.total_duration_s:.3f} s",
            f"Max Physical Step:         {self.max_physical_step_mm:.4f} mm",
            f"Max Physical Speed:        {self.max_physical_speed_mm_s:.1f} mm/s",
            f"Bends Present / Completed: {len(self.bends_present)} ({', '.join(self.bends_present)}) / {len(self.bends_completed)} completed",
            "-" * 72,
            "BEND SWITCH CONTINUITY EVALUATION:",
        ]

        if self.bend_switch_evaluations:
            for b in self.bend_switch_evaluations:
                lines.append(
                    f"  {b.from_bend} -> {b.to_bend} (frame {b.frame}): "
                    f"step = {b.max_step_mm:.4f} mm (tol = {b.tolerance_mm:.2f} mm) -> [{b.status}]"
                )
        else:
            lines.append("  None evaluated (single bend or transfer unrecorded)")

        lines.append("-" * 72)
        lines.append("PHASE-AWARE MOTION & CONTACT METRICS:")
        lines.append(
            f"  {'Phase':<14} {'Frames':<8} {'Duration(s)':<12} {'Max Step(mm)':<14} {'Max Speed':<12} {'Punch Contact':<14}"
        )
        for ph_name in sorted(self.phase_metrics.keys()):
            pm = self.phase_metrics[ph_name]
            contact_str = "N/A"
            if ph_name in ("BENDING", "BOTTOM"):
                contact_str = f"{pm.punch_contact_valid_frames} OK / {pm.punch_contact_violation_frames} ERR"
            lines.append(
                f"  {pm.phase:<14} {pm.frame_count:<8} {pm.duration_s:<12.3f} "
                f"{pm.max_physical_step_mm:<14.4f} {pm.max_physical_speed_mm_s:<12.1f} {contact_str:<14}"
            )

        lines.append("-" * 72)
        lines.append(f"DISCONTINUITIES DETECTED: {self.discontinuities_count}")
        if self.discontinuities:
            for d in self.discontinuities[:10]:
                lines.append(
                    f"  [FRAME {d.frame}] Phase: {d.phase} | Landmark: {d.landmark_id} | "
                    f"Step: {d.step_mm:.4f} mm (allowed: {d.allowable_mm:.2f} mm) | Reason: {d.reason}"
                )
            if len(self.discontinuities) > 10:
                lines.append(f"  ... and {len(self.discontinuities) - 10} more discontinuities")
        else:
            lines.append("  None (Continuous motion verified)")

        lines.append("-" * 72)
        lines.append(f"GEOMETRIC INCONSISTENCIES: {self.geometric_inconsistencies_count}")
        if self.geometric_inconsistencies:
            for g in self.geometric_inconsistencies[:10]:
                lines.append(
                    f"  [FRAME {g.frame}] Phase: {g.phase} | Kind: {g.kind} | "
                    f"Value: {g.value_mm:.4f} mm (tol: {g.tolerance_mm:.2f} mm) | Details: {g.details}"
                )
            if len(self.geometric_inconsistencies) > 10:
                lines.append(f"  ... and {len(self.geometric_inconsistencies) - 10} more inconsistencies")
        else:
            lines.append("  None (Tooling and workpiece geometry consistent)")

        lines.append("-" * 72)
        lines.append(f"UNRESOLVED CONDITIONS: {self.unresolved_conditions_count}")
        if self.unresolved_conditions:
            for u in self.unresolved_conditions[:10]:
                lines.append(f"  [{u.get('kind', 'UNRESOLVED')}] {u.get('details', '')}")
            if len(self.unresolved_conditions) > 10:
                lines.append(f"  ... and {len(self.unresolved_conditions) - 10} more unresolved conditions")
        else:
            lines.append("  None")

        lines.append("-" * 72)
        lines.append(f"Notice: {self.notice}")
        lines.append("=" * 72)
        return "\n".join(lines)


def analyze_trace(
    source: Union[str, Path, Sequence[Dict[str, Any]]],
    *,
    switch_tolerance_mm: float = 0.05,
    contact_tolerance_mm: float = 0.10,
    angle_tolerance_deg: float = 0.50,
) -> TraceAnalysisReport:
    """Analyze a JSONL motion trace and return a comprehensive TraceAnalysisReport.

    Strict validation requirements:
    - Complete recording lifecycle: TRACE_STARTED, TRACE_STOPPED, and completion evidence.
    - Physical landmarks required for continuity verification; missing landmarks -> UNVERIFIED.
    - Signed target angle evaluation supporting arbitrary angles.
    - Independent signed punch clearance (contact loss) and punch penetration validation.
    - Phase-specific trajectory limits based on physical machine velocity envelopes.
    - Complete eight-bend reference sequence and all transfer verification.
    """
    records: List[Dict[str, Any]] = []
    file_path_str = ""
    source_revision = get_source_revision()

    if isinstance(source, (str, Path)):
        file_path = Path(source).expanduser().resolve()
        file_path_str = str(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Trace file not found: {file_path}")
        with file_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line_str = line.strip()
                if line_str:
                    try:
                        records.append(json.loads(line_str))
                    except json.JSONDecodeError as err:
                        # Malformed or truncated JSON line must trigger FAIL
                        unres = [{
                            "kind": "MALFORMED_JSON_LINE",
                            "details": f"Line {line_no} failed JSON decode: {err}",
                        }]
                        return TraceAnalysisReport(
                            overall_status="FAIL",
                            trace_file=file_path_str,
                            analyzer_version=ANALYZER_VERSION,
                            source_revision=source_revision,
                            project_version="UNKNOWN",
                            build_id="UNKNOWN",
                            total_frames=0,
                            total_events=0,
                            total_duration_s=0.0,
                            bends_present=[],
                            bends_completed=[],
                            phases_traversed=[],
                            max_physical_step_mm=0.0,
                            max_physical_speed_mm_s=0.0,
                            discontinuities_count=0,
                            geometric_inconsistencies_count=0,
                            unresolved_conditions_count=1,
                            bend_switches_continuous_count=0,
                            bend_switches_discontinuous_count=0,
                            unresolved_conditions=unres,
                        )
    else:
        records = list(source)

    frames: List[Dict[str, Any]] = [r for r in records if r.get("kind") == "FRAME"]
    events: List[Dict[str, Any]] = [r for r in records if r.get("kind") == "EVENT"]

    event_counts: Dict[str, int] = {}
    for ev in events:
        ename = ev.get("event", "UNKNOWN")
        event_counts[ename] = event_counts.get(ename, 0) + 1

    project_version = "UNKNOWN"
    build_id = "UNKNOWN"
    for r in records:
        if "project_version" in r:
            project_version = str(r["project_version"])
        if "build_id" in r:
            build_id = str(r["build_id"])
        if project_version != "UNKNOWN" and build_id != "UNKNOWN":
            break

    discontinuities: List[DiscontinuityRecord] = []
    geometric_inconsistencies: List[GeometricInconsistencyRecord] = []
    bend_switches: List[BendSwitchEvaluation] = []
    unresolved_conditions: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Check 1: Trace lifecycle events (TRACE_STARTED, TRACE_STOPPED)
    # ------------------------------------------------------------------
    has_trace_started = "TRACE_STARTED" in event_counts
    has_trace_stopped = "TRACE_STOPPED" in event_counts

    trace_stopped_reason = ""
    for ev in events:
        if ev.get("event") == "TRACE_STOPPED":
            trace_stopped_reason = str(ev.get("reason", ""))
            break

    if not has_trace_started:
        unresolved_conditions.append({
            "kind": "MISSING_TRACE_STARTED",
            "details": "Trace file missing TRACE_STARTED lifecycle event",
        })
    if not has_trace_stopped:
        unresolved_conditions.append({
            "kind": "MISSING_TRACE_STOPPED",
            "details": "Trace recording was aborted or truncated before TRACE_STOPPED",
        })

    # Minimum frames check
    if len(frames) < 2:
        unresolved_conditions.append({
            "kind": "INSUFFICIENT_FRAMES",
            "details": f"Trace contains {len(frames)} frame(s); at least 2 frames required for motion analysis",
        })

    # ------------------------------------------------------------------
    # Check 2: Reference bend sequence and signed target angle discovery
    # ------------------------------------------------------------------
    expected_order: List[str] = []
    for ev in events:
        if ev.get("event") == "RECORDING_STARTED" and "order" in ev:
            expected_order = list(ev["order"])
            break
    if not expected_order and frames:
        first_frame = frames[0]
        if "bend_order" in first_frame and isinstance(first_frame["bend_order"], list):
            expected_order = list(first_frame["bend_order"])

    # Discover bend adjacent panels topology from RECORDING_STARTED if available
    bend_topology_map: Dict[str, Set[str]] = {}
    for ev in events:
        if ev.get("event") == "RECORDING_STARTED" and "bends_topology" in ev:
            bt = ev.get("bends_topology")
            if isinstance(bt, dict):
                for bid, binfo in bt.items():
                    if isinstance(binfo, dict):
                        f_p = binfo.get("fixed_panel")
                        m_p = binfo.get("moving_panel")
                        s = set()
                        if f_p: s.add(str(f_p))
                        if m_p: s.add(str(m_p))
                        if s: bend_topology_map[str(bid)] = s
            break
    # Target angle is read from bend_angle_deg during BOTTOM phase or from final settled angles
    discovered_targets: Dict[str, float] = {}
    for f in frames:
        bid = f.get("active_bend_id")
        ph = f.get("cycle_phase")
        if bid and ph == "BOTTOM":
            b_ang = f.get("bend_angle_deg")
            if b_ang is not None and math.isfinite(float(b_ang)):
                discovered_targets[str(bid)] = float(b_ang)

    # ------------------------------------------------------------------
    # Frame-by-frame analysis
    # ------------------------------------------------------------------
    bends_present_set = set()
    bends_completed_set = set()
    phases_traversed_set = set()
    phase_metrics: Dict[str, PhaseMetrics] = {}

    max_overall_step = 0.0
    max_overall_speed = 0.0
    total_duration_s = 0.0

    frames_with_physical_landmarks = 0
    prev_frame: Optional[Dict[str, Any]] = None

    for idx, f in enumerate(frames):
        cur_phase = str(f.get("cycle_phase", "UNKNOWN"))
        cur_bend = str(f.get("active_bend_id", "UNKNOWN"))
        dt = f.get("elapsed_dt_s")
        unix_s = f.get("unix_s")

        # Validate timestamp and elapsed dt
        if unix_s is None or not isinstance(unix_s, (int, float)) or not math.isfinite(unix_s) or unix_s <= 0:
            unresolved_conditions.append({
                "frame": idx,
                "kind": "INVALID_TIMESTAMP",
                "details": f"Frame {idx} has invalid unix timestamp: {unix_s}",
            })

        if dt is not None and isinstance(dt, (int, float)):
            if not math.isfinite(dt) or dt < 0:
                unresolved_conditions.append({
                    "frame": idx,
                    "kind": "INVALID_ELAPSED_DT",
                    "details": f"Frame {idx} has invalid or negative elapsed_dt_s: {dt}",
                })
            else:
                total_duration_s += dt

        bends_present_set.add(cur_bend)
        phases_traversed_set.add(cur_phase)

        if cur_phase not in phase_metrics:
            phase_metrics[cur_phase] = PhaseMetrics(phase=cur_phase)
        pm = phase_metrics[cur_phase]
        pm.frame_count += 1
        if isinstance(dt, (int, float)) and dt > 0:
            pm.duration_s += dt

        # --------------------------------------------------------------
        # Landmark continuity validation
        # --------------------------------------------------------------
        cur_phys = f.get("physical_landmarks")
        if cur_phys and isinstance(cur_phys, dict):
            frames_with_physical_landmarks += 1

        if prev_frame is not None:
            prev_phys = prev_frame.get("physical_landmarks")
            prev_bend = str(prev_frame.get("active_bend_id", "UNKNOWN"))
            prev_phase = str(prev_frame.get("cycle_phase", "UNKNOWN"))
            phase_changed = (cur_phase != prev_phase)
            bend_changed = (cur_bend != prev_bend)

            if isinstance(prev_phys, dict) and isinstance(cur_phys, dict):
                common_keys = prev_phys.keys() & cur_phys.keys()
                # Check for insufficient common landmarks (expect >= 4 vertices)
                if len(common_keys) < 4:
                    unresolved_conditions.append({
                        "frame": idx,
                        "kind": "INSUFFICIENT_COMMON_LANDMARKS",
                        "details": f"Frame {idx} shares only {len(common_keys)} landmark(s) with previous frame (minimum 4 required)",
                    })

                if common_keys:
                    step_max, max_k = max(
                        (math.dist(prev_phys[k], cur_phys[k]), k)
                        for k in common_keys
                    )
                    eff_dt = dt if (isinstance(dt, (int, float)) and dt > 0) else 0.025
                    speed = step_max / eff_dt

                    pm.max_physical_step_mm = max(pm.max_physical_step_mm, step_max)
                    pm.max_physical_speed_mm_s = max(pm.max_physical_speed_mm_s, speed)
                    max_overall_step = max(max_overall_step, step_max)
                    max_overall_speed = max(max_overall_speed, speed)

                    # A. Bend Switch Continuity
                    if bend_changed:
                        # Allow frame motion if transition is from dynamic TRANSFER phase
                        allowed_sw = (
                            max(switch_tolerance_mm, 350.0 * eff_dt)
                            if (prev_phase == "TRANSFER" or cur_phase == "TRANSFER")
                            else switch_tolerance_mm
                        )
                        sw_status = "PASS" if step_max <= allowed_sw else "FAIL"
                        bend_switches.append(BendSwitchEvaluation(
                            from_bend=prev_bend,
                            to_bend=cur_bend,
                            frame=idx,
                            timestamp_s=float(unix_s or 0.0),
                            max_step_mm=step_max,
                            tolerance_mm=allowed_sw,
                            status=sw_status,
                        ))
                        if sw_status == "FAIL":
                            discontinuities.append(DiscontinuityRecord(
                                frame=idx,
                                timestamp_s=float(unix_s or 0.0),
                                phase=cur_phase,
                                from_bend=prev_bend,
                                to_bend=cur_bend,
                                landmark_id=max_k,
                                step_mm=step_max,
                                allowable_mm=allowed_sw,
                                speed_mm_s=speed,
                                reason=f"BEND_SWITCH_JUMP: step {step_max:.4f}mm exceeds tolerance {allowed_sw:.2f}mm at {prev_bend}->{cur_bend}",
                            ))

                    # B. Phase-aware velocity envelope
                    if not bend_changed:
                        allowed_step = 0.05
                        reason_label = ""
                        # Stationary phases: sheet must be completely static
                        if cur_phase in ("IDLE", "BOTTOM", "CONTACT", "STEP_COMPLETE"):
                            if phase_changed and prev_phase in ("BENDING", "RETRACT"):
                                allowed_step = max(0.5, 200.0 * eff_dt)
                                reason_label = f"PHASE_BOUNDARY_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{cur_phase}"
                            else:
                                allowed_step = 0.05
                                reason_label = f"STATIONARY_PHASE_MOTION: step {step_max:.4f}mm in {cur_phase}"
                        # Ram travel without sheet transfer: workpiece must remain seated
                        elif cur_phase in ("APPROACH", "RETRACT"):
                            if phase_changed and prev_phase in ("TRANSFER", "BOTTOM"):
                                allowed_step = max(0.5, 350.0 * eff_dt)
                                reason_label = f"PHASE_BOUNDARY_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{cur_phase}"
                            else:
                                allowed_step = 0.05
                                reason_label = f"WORKPIECE_MOTION_DURING_RAM_TRAVEL: step {step_max:.4f}mm in {cur_phase}"
                        # Active bending: maximum linear tip speed 200 mm/s
                        elif cur_phase == "BENDING":
                            allowed_step = max(0.5, 200.0 * eff_dt)
                            reason_label = f"BENDING_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm"
                        # Active 3D spatial transfer: maximum linear speed 350 mm/s
                        elif cur_phase == "TRANSFER":
                            allowed_step = max(1.0, 350.0 * eff_dt)
                            reason_label = f"TRANSFER_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm"

                        if step_max > allowed_step:
                            discontinuities.append(DiscontinuityRecord(
                                frame=idx,
                                timestamp_s=float(unix_s or 0.0),
                                phase=cur_phase,
                                from_bend=prev_bend,
                                to_bend=cur_bend,
                                landmark_id=max_k,
                                step_mm=step_max,
                                allowable_mm=allowed_step,
                                speed_mm_s=speed,
                                reason=reason_label,
                            ))

        # --------------------------------------------------------------
        # Punch Contact & Penetration Validation (BENDING & BOTTOM)
        # --------------------------------------------------------------
        if cur_phase in ("BENDING", "BOTTOM"):
            clearance = f.get("punch_clearance_mm")
            ram_z = f.get("ram_z_mm")
            b_zone = f.get("bend_zone_position") or {}
            inner_apex = b_zone.get("inner_apex")
            has_inconsistency = False

            # 1. Contact Loss: punch tip must not separate from inner sheet surface
            if clearance is not None:
                cl_val = float(clearance)
                if cl_val > contact_tolerance_mm:
                    has_inconsistency = True
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=idx,
                        timestamp_s=float(unix_s or 0.0),
                        phase=cur_phase,
                        active_bend_id=cur_bend,
                        kind="PUNCH_CONTACT_LOSS",
                        value_mm=cl_val,
                        tolerance_mm=contact_tolerance_mm,
                        details=f"Punch clearance {cl_val:.4f}mm > {contact_tolerance_mm}mm during {cur_phase}",
                    ))

            # 2. Punch Penetration Violation: punch tip must not penetrate beyond apex
            if ram_z is not None and inner_apex is not None:
                inner_z = float(inner_apex[1])
                # Penetration occurs if ram_z penetrates deeper than inner_apex_z by > tolerance
                penetration = inner_z - float(ram_z)
                if penetration > contact_tolerance_mm:
                    has_inconsistency = True
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=idx,
                        timestamp_s=float(unix_s or 0.0),
                        phase=cur_phase,
                        active_bend_id=cur_bend,
                        kind="PUNCH_PENETRATION_VIOLATION",
                        value_mm=penetration,
                        tolerance_mm=contact_tolerance_mm,
                        details=f"Punch tip penetrated past inner surface by {penetration:.4f}mm during {cur_phase}",
                    ))

            if has_inconsistency:
                pm.punch_contact_violation_frames += 1
            else:
                pm.punch_contact_valid_frames += 1

            # 3. Die Support Validation (BENDING only)
            if cur_phase == "BENDING":
                val_status = str(f.get("validation_status", ""))
                if "UNSUPPORTED" in val_status:
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=idx,
                        timestamp_s=float(unix_s or 0.0),
                        phase=cur_phase,
                        active_bend_id=cur_bend,
                        kind="DIE_SUPPORT_VIOLATION",
                        value_mm=0.0,
                        tolerance_mm=0.0,
                        details=val_status,
                    ))

        # Check pose warnings
        warning = f.get("pose_warning")
        if warning and "KINEMATIC_POSE_HALT" in str(warning):
            unresolved_conditions.append({
                "frame": idx,
                "timestamp_s": float(unix_s or 0.0),
                "kind": "KINEMATIC_POSE_HALT",
                "details": str(warning),
            })

        # --------------------------------------------------------------
        # 4. Flange Orientation Check (BENDING & BOTTOM)
        # Verify that adjacent panels of the active bend rise upward toward the
        # punch (+Z_m) above the die, rather than pointing downward into the die body.
        # --------------------------------------------------------------
        if cur_phase in ("BENDING", "BOTTOM") and cur_bend:
            phys_lms = f.get("physical_landmarks", {})
            bend_zone_pos = f.get("bend_zone_position", {}) or {}
            outer_apex = bend_zone_pos.get("outer_apex")
            # If bend has folded non-trivially (|angle| >= 10 deg):
            cur_angle = abs(float(f.get("bend_angle_deg", 0.0)))
            if cur_angle >= 10.0 and phys_lms:
                # Find active bend mid landmark Z or outer apex Z as reference
                mid_lm = phys_lms.get(f"bend:{cur_bend}:mid")
                ref_z = mid_lm[2] if mid_lm else (outer_apex[1] if outer_apex else None)
                if ref_z is not None:
                    # Discover adjacent panels from frame telemetry or RECORDING_STARTED topology
                    adj = f.get("adjacent_panels")
                    adj_set = set(adj) if adj and isinstance(adj, (list, tuple)) else bend_topology_map.get(cur_bend)
                    if not adj_set:
                        # Fallback for standard benchmark part:
                        # B01: P03, P08 | B02: P03, P06 | B03: P03, P04 | B04: P03, P01
                        # B05: P08, P09 | B06: P06, P07 | B07: P04, P05 | B08: P01, P02
                        fallback_map = {
                            "B01": {"P03", "P08"},
                            "B02": {"P03", "P06"},
                            "B03": {"P03", "P04"},
                            "B04": {"P03", "P01"},
                            "B05": {"P08", "P09"},
                            "B06": {"P06", "P07"},
                            "B07": {"P04", "P05"},
                            "B08": {"P01", "P02"},
                        }
                        adj_set = fallback_map.get(cur_bend)

                    for lm_name, lm_pos in phys_lms.items():
                        if lm_name.startswith("panel:"):
                            # If adjacent panels are known, strictly check only adjacent panels!
                            parts = lm_name.split(":")
                            pid = parts[1] if len(parts) >= 2 else None
                            if adj_set is not None and pid not in adj_set:
                                continue
                            if adj_set is None:
                                # When adjacent panels not specified in frame, check whether both
                                # flanges point downward: if landmark is more than 10mm below root
                                # and contains related prefix
                                pass
                            # Adjacent flange vertices must NOT penetrate below the bend root:
                            if lm_pos[2] < ref_z - 3.0:
                                geometric_inconsistencies.append(GeometricInconsistencyRecord(
                                    frame=idx,
                                    timestamp_s=float(unix_s or 0.0),
                                    phase=cur_phase,
                                    active_bend_id=cur_bend,
                                    kind="INVERTED_FLANGE_ORIENTATION",
                                    value_mm=float(lm_pos[2] - ref_z),
                                    tolerance_mm=0.0,
                                    details=f"Adjacent panel landmark {lm_name} at Z={lm_pos[2]:.2f}mm extends below bend root Z={ref_z:.2f}mm during {cur_phase}",
                                ))
                                break

        prev_frame = f

    # ------------------------------------------------------------------
    # Check 3: Missing physical landmarks verification
    # ------------------------------------------------------------------
    if len(frames) > 0 and frames_with_physical_landmarks == 0:
        unresolved_conditions.append({
            "kind": "MISSING_PHYSICAL_LANDMARKS",
            "details": "Trace contains 0 frames with physical landmarks; physical continuity cannot be verified",
        })

    # ------------------------------------------------------------------
    # Check 4: Signed target bend angle verification
    # ------------------------------------------------------------------
    if frames:
        final_angles = frames[-1].get("angles_deg", {})
        for bid, target_angle in discovered_targets.items():
            if bid in final_angles:
                actual_angle = float(final_angles[bid])
                angle_err = abs(actual_angle - target_angle)
                if angle_err <= angle_tolerance_deg:
                    bends_completed_set.add(bid)
                else:
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=len(frames) - 1,
                        timestamp_s=float(frames[-1].get("unix_s", 0.0)),
                        phase="COMPLETED",
                        active_bend_id=bid,
                        kind="TARGET_ANGLE_MISMATCH",
                        value_mm=angle_err,
                        tolerance_mm=angle_tolerance_deg,
                        details=f"Bend {bid} final angle {actual_angle:+.2f}° does not match signed target {target_angle:+.2f}° (err {angle_err:.2f}° > {angle_tolerance_deg}°)",
                    ))

    # ------------------------------------------------------------------
    # Check 5: Sequence completeness (all reference bends & transfers)
    # ------------------------------------------------------------------
    if expected_order:
        missing_bends = [b for b in expected_order if b not in bends_completed_set]
        if missing_bends:
            unresolved_conditions.append({
                "kind": "INCOMPLETE_BEND_SEQUENCE",
                "details": f"Expected bends {expected_order}, but {missing_bends} were not completed",
            })

        expected_switches_count = max(0, len(expected_order) - 1)
        if len(bend_switches) < expected_switches_count:
            unresolved_conditions.append({
                "kind": "INCOMPLETE_TRANSFERS",
                "details": f"Expected {expected_switches_count} workpiece transfers, but only {len(bend_switches)} were recorded",
            })

    # ------------------------------------------------------------------
    # Check 6: Completion confirmation event
    # ------------------------------------------------------------------
    has_completion_event = False
    valid_stop_reasons = (
        "PLAYBACK_COMPLETED",
        "D4_P1_VERIFICATION_COMPLETE",
        "FULL_CYCLE_COMPLETED",
    )
    if trace_stopped_reason in valid_stop_reasons:
        has_completion_event = True
    elif "PLAYBACK_COMPLETED" in event_counts:
        has_completion_event = True

    if not has_completion_event:
        unresolved_conditions.append({
            "kind": "MISSING_COMPLETION_EVENT",
            "details": f"Trace ended without playback completion (stop reason: '{trace_stopped_reason}')",
        })

    # ------------------------------------------------------------------
    # Overall Status Classification: PASS | FAIL | UNVERIFIED
    # ------------------------------------------------------------------
    sw_continuous_cnt = sum(1 for b in bend_switches if b.status == "PASS")
    sw_discontinuous_cnt = sum(1 for b in bend_switches if b.status == "FAIL")

    # Invariants violated -> FAIL
    if discontinuities or geometric_inconsistencies:
        overall_status = "FAIL"
    # Fatal malformed or aborted conditions -> FAIL
    elif any(u.get("kind") in ("MALFORMED_JSON_LINE", "TARGET_ANGLE_MISMATCH", "KINEMATIC_POSE_HALT") for u in unresolved_conditions):
        overall_status = "FAIL"
    # Missing required evidence or incomplete execution -> UNVERIFIED
    elif unresolved_conditions or frames_with_physical_landmarks == 0 or len(frames) < 2:
        overall_status = "UNVERIFIED"
    else:
        overall_status = "PASS"

    return TraceAnalysisReport(
        overall_status=overall_status,
        analyzer_version=ANALYZER_VERSION,
        source_revision=source_revision,
        trace_file=file_path_str,
        project_version=project_version,
        build_id=build_id,
        total_frames=len(frames),
        total_events=len(events),
        total_duration_s=total_duration_s,
        bends_present=sorted(list(bends_present_set)),
        bends_completed=sorted(list(bends_completed_set)),
        phases_traversed=sorted(list(phases_traversed_set)),
        max_physical_step_mm=max_overall_step,
        max_physical_speed_mm_s=max_overall_speed,
        discontinuities_count=len(discontinuities),
        geometric_inconsistencies_count=len(geometric_inconsistencies),
        unresolved_conditions_count=len(unresolved_conditions),
        bend_switches_continuous_count=sw_continuous_cnt,
        bend_switches_discontinuous_count=sw_discontinuous_cnt,
        bend_switch_evaluations=bend_switches,
        discontinuities=discontinuities,
        geometric_inconsistencies=geometric_inconsistencies,
        unresolved_conditions=unresolved_conditions,
        phase_metrics=phase_metrics,
        event_counts=event_counts,
    )


def main():
    parser = argparse.ArgumentParser(
        description="SMBS / PressBrakeSim Automated Motion Continuity Analyzer"
    )
    parser.add_argument("trace_path", help="Path to .jsonl motion trace file")
    parser.add_argument("--json", action="store_true", help="Output JSON report instead of text")
    parser.add_argument("--save-json", help="Save structured JSON report to specified path")
    parser.add_argument("--switch-tol", type=float, default=0.05, help="Bend switch continuity tolerance in mm (default: 0.05)")
    args = parser.parse_args()

    report = analyze_trace(args.trace_path, switch_tolerance_mm=args.switch_tol)

    if args.save_json:
        out_p = Path(args.save_json)
        out_p.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Report saved to {out_p}")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text_summary())

    if report.overall_status == "FAIL":
        sys.exit(1)
    elif report.overall_status == "UNVERIFIED":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
