"""Automated motion trace analyzer for SMBS / PressBrakeSim (Stage D4-P2).

Evaluates JSONL trace files produced by MotionTraceRecorder:
1. Physical landmark continuity across every cycle phase.
2. Phase-aware velocity envelopes and jump detection.
3. Seamless endpoint continuity at bend switches and transfer transitions.
4. Punch-to-sheet contact validation during BENDING and BOTTOM.
5. Die shoulder support tracking during BENDING without assuming two-point support elsewhere.
6. Summary of detected discontinuities, geometric inconsistencies, and unresolved conditions.
7. Outputs PASS, FAIL, or UNVERIFIED rather than claiming unsupported physical certification.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


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
    kind: str
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
    status: str  # "PASS" | "FAIL"


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
            f"SMBS / PressBrakeSim Motion Continuity Report (Stage D4-P2)",
            "=" * 72,
            f"Overall Status:            {self.overall_status}",
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
            lines.append("  (No bend switches detected in trace)")

        lines.extend([
            "-" * 72,
            "PHASE-AWARE MOTION & CONTACT METRICS:",
            f"  {'Phase':<14} {'Frames':<8} {'Duration(s)':<12} {'Max Step(mm)':<14} {'Max Speed':<12} {'Punch Contact':<14}",
        ])
        for p, m in self.phase_metrics.items():
            if m.phase in ("BENDING", "BOTTOM"):
                c_str = f"{m.punch_contact_valid_frames} OK / {m.punch_contact_violation_frames} ERR"
            else:
                c_str = "N/A"
            lines.append(
                f"  {m.phase:<14} {m.frame_count:<8} {m.duration_s:<12.3f} {m.max_physical_step_mm:<14.4f} "
                f"{m.max_physical_speed_mm_s:<12.1f} {c_str:<14}"
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
        lines.append(f"Notice: {self.notice}")
        lines.append("=" * 72)
        return "\n".join(lines)


def analyze_trace(
    source: Union[str, Path, Sequence[Dict[str, Any]]],
    *,
    switch_tolerance_mm: float = 0.05,
    contact_tolerance_mm: float = 0.10,
) -> TraceAnalysisReport:
    """Analyze a JSONL motion trace and return a comprehensive TraceAnalysisReport."""
    records: List[Dict[str, Any]] = []
    file_path_str = ""

    if isinstance(source, (str, Path)):
        file_path = Path(source).expanduser().resolve()
        file_path_str = str(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Trace file not found: {file_path}")
        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    records.append(json.loads(line_str))
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

    bends_present_set = set()
    bends_completed_set = set()
    phases_traversed_set = set()
    phase_metrics: Dict[str, PhaseMetrics] = {}

    discontinuities: List[DiscontinuityRecord] = []
    geometric_inconsistencies: List[GeometricInconsistencyRecord] = []
    bend_switches: List[BendSwitchEvaluation] = []
    unresolved_conditions: List[Dict[str, Any]] = []

    max_overall_step = 0.0
    max_overall_speed = 0.0
    total_duration_s = 0.0

    prev_frame: Optional[Dict[str, Any]] = None

    for idx, f in enumerate(frames):
        cur_phase = str(f.get("cycle_phase", "UNKNOWN"))
        cur_bend = str(f.get("active_bend_id", "UNKNOWN"))
        dt = f.get("elapsed_dt_s") or 0.025
        if isinstance(dt, (int, float)) and dt > 0:
            total_duration_s += dt

        bends_present_set.add(cur_bend)
        phases_traversed_set.add(cur_phase)

        if cur_phase not in phase_metrics:
            phase_metrics[cur_phase] = PhaseMetrics(phase=cur_phase)
        pm = phase_metrics[cur_phase]
        pm.frame_count += 1
        pm.duration_s += dt if isinstance(dt, (int, float)) else 0.0

        # Check bend completion
        angles = f.get("angles_deg", {})
        if cur_bend in angles and abs(angles[cur_bend]) >= 89.9:
            bends_completed_set.add(cur_bend)

        # ------------------------------------------------------------------
        # 1. Physical Landmark Continuity Validation
        # ------------------------------------------------------------------
        cur_phys = f.get("physical_landmarks") or {}
        if prev_frame is not None and cur_phys:
            prev_phys = prev_frame.get("physical_landmarks") or {}
            common_keys = prev_phys.keys() & cur_phys.keys()

            if common_keys:
                step_max, max_k = max(
                    (math.dist(prev_phys[k], cur_phys[k]), k)
                    for k in common_keys
                )
                speed = step_max / dt if (isinstance(dt, (int, float)) and dt > 0) else 0.0

                pm.max_physical_step_mm = max(pm.max_physical_step_mm, step_max)
                pm.max_physical_speed_mm_s = max(pm.max_physical_speed_mm_s, speed)
                max_overall_step = max(max_overall_step, step_max)
                max_overall_speed = max(max_overall_speed, speed)

                prev_bend = str(prev_frame.get("active_bend_id", "UNKNOWN"))
                bend_changed = cur_bend != prev_bend

                prev_phase = str(prev_frame.get("cycle_phase", "UNKNOWN"))
                phase_changed = (cur_phase != prev_phase)

                # Check bend switch transition continuity
                if bend_changed:
                    # Allow frame-interval motion across transfer phase transition
                    allowed_sw = (
                        max(switch_tolerance_mm, 500.0 * (dt or 0.05))
                        if (prev_phase == "TRANSFER" or cur_phase == "TRANSFER")
                        else switch_tolerance_mm
                    )
                    sw_status = "PASS" if step_max <= allowed_sw else "FAIL"
                    bend_switches.append(BendSwitchEvaluation(
                        from_bend=prev_bend,
                        to_bend=cur_bend,
                        frame=idx,
                        timestamp_s=float(f.get("unix_s", 0.0)),
                        max_step_mm=step_max,
                        tolerance_mm=allowed_sw,
                        status=sw_status,
                    ))
                    if sw_status == "FAIL":
                        discontinuities.append(DiscontinuityRecord(
                            frame=idx,
                            timestamp_s=float(f.get("unix_s", 0.0)),
                            phase=cur_phase,
                            from_bend=prev_bend,
                            to_bend=cur_bend,
                            landmark_id=max_k,
                            step_mm=step_max,
                            allowable_mm=allowed_sw,
                            speed_mm_s=speed,
                            reason=f"BEND_SWITCH_JUMP: physical step {step_max:.4f}mm exceeds {allowed_sw:.2f}mm",
                        ))

                # Check phase-aware motion envelope
                if not bend_changed:
                    allowed_step = 0.05
                    reason_label = ""
                    if cur_phase in ("IDLE", "BOTTOM", "CONTACT", "STEP_COMPLETE"):
                        if phase_changed and prev_phase in ("BENDING", "RETRACT"):
                            allowed_step = max(0.5, 500.0 * (dt or 0.05))
                            reason_label = f"PHASE_TRANSITION_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{cur_phase}"
                        else:
                            allowed_step = 0.05
                            reason_label = f"STATIONARY_PHASE_MOTION: step {step_max:.4f}mm in {cur_phase}"
                    elif cur_phase in ("APPROACH", "RETRACT"):
                        if phase_changed and prev_phase in ("TRANSFER", "BOTTOM"):
                            allowed_step = max(0.5, 500.0 * (dt or 0.05))
                            reason_label = f"PHASE_TRANSITION_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm at {prev_phase}->{cur_phase}"
                        else:
                            allowed_step = 0.05
                            reason_label = f"WORKPIECE_MOTION_DURING_RAM_TRAVEL: step {step_max:.4f}mm in {cur_phase}"
                    elif cur_phase == "BENDING":
                        allowed_step = max(0.5, 500.0 * (dt or 0.05))
                        reason_label = f"BENDING_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm"
                    elif cur_phase == "TRANSFER":
                        allowed_step = max(1.0, 1200.0 * (dt or 0.05))
                        reason_label = f"TRANSFER_STEP_EXCEEDED: step {step_max:.4f}mm > {allowed_step:.2f}mm"

                    if step_max > allowed_step:
                        discontinuities.append(DiscontinuityRecord(
                            frame=idx,
                            timestamp_s=float(f.get("unix_s", 0.0)),
                            phase=cur_phase,
                            from_bend=prev_bend,
                            to_bend=cur_bend,
                            landmark_id=max_k,
                            step_mm=step_max,
                            allowable_mm=allowed_step,
                            speed_mm_s=speed,
                            reason=reason_label,
                        ))

        # ------------------------------------------------------------------
        # 2. Punch Contact & Clearance Validation (BENDING & BOTTOM)
        # ------------------------------------------------------------------
        if cur_phase in ("BENDING", "BOTTOM"):
            clearance = f.get("punch_clearance_mm")
            ram_z = f.get("ram_z_mm")
            b_zone = f.get("bend_zone_position") or {}
            inner_apex = b_zone.get("inner_apex")
            has_inconsistency = False

            if clearance is not None and clearance > contact_tolerance_mm:
                has_inconsistency = True
                geometric_inconsistencies.append(GeometricInconsistencyRecord(
                    frame=idx,
                    timestamp_s=float(f.get("unix_s", 0.0)),
                    phase=cur_phase,
                    active_bend_id=cur_bend,
                    kind="PUNCH_CONTACT_LOSS",
                    value_mm=float(clearance),
                    tolerance_mm=contact_tolerance_mm,
                    details=f"Punch clearance {clearance:.4f}mm > {contact_tolerance_mm}mm during {cur_phase}",
                ))
            elif ram_z is not None and inner_apex is not None:
                inner_z = float(inner_apex[1])
                gap = abs(float(ram_z) - inner_z)
                if float(ram_z) < inner_z - contact_tolerance_mm:
                    has_inconsistency = True
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=idx,
                        timestamp_s=float(f.get("unix_s", 0.0)),
                        phase=cur_phase,
                        active_bend_id=cur_bend,
                        kind="PUNCH_PENETRATION_VIOLATION",
                        value_mm=gap,
                        tolerance_mm=contact_tolerance_mm,
                        details=f"Punch tip penetrated past inner surface by {gap:.4f}mm during {cur_phase}",
                    ))

            if has_inconsistency:
                pm.punch_contact_violation_frames += 1
            else:
                pm.punch_contact_valid_frames += 1

            # --------------------------------------------------------------
            # 3. Die Support Validation (BENDING only)
            # --------------------------------------------------------------
            if cur_phase == "BENDING":
                val_status = str(f.get("validation_status", ""))
                if "UNSUPPORTED" in val_status:
                    geometric_inconsistencies.append(GeometricInconsistencyRecord(
                        frame=idx,
                        timestamp_s=float(f.get("unix_s", 0.0)),
                        phase=cur_phase,
                        active_bend_id=cur_bend,
                        kind="DIE_SUPPORT_VIOLATION",
                        value_mm=0.0,
                        tolerance_mm=0.0,
                        details=val_status,
                    ))

        # ------------------------------------------------------------------
        # 4. Unresolved Conditions / Faults
        # ------------------------------------------------------------------
        warning = f.get("pose_warning")
        if warning and "KINEMATIC_POSE_HALT" in str(warning):
            unresolved_conditions.append({
                "frame": idx,
                "timestamp_s": float(f.get("unix_s", 0.0)),
                "kind": "KINEMATIC_POSE_HALT",
                "details": str(warning),
            })

        prev_frame = f

    # Event analysis for faults
    for ev in events:
        ename = ev.get("event")
        if ename in ("PHYSICAL_DISCONTINUITY", "BEND_SWITCH_DISCONTINUITY"):
            # Ensure recorded in summary counts
            pass
        elif ename == "TRANSFER_TRAJECTORY_FAILED":
            unresolved_conditions.append({
                "kind": "TRANSFER_TRAJECTORY_FAILED",
                "details": str(ev.get("reason", "Trajectory generation failed")),
            })

    # Overall Status determination
    sw_continuous_cnt = sum(1 for b in bend_switches if b.status == "PASS")
    sw_discontinuous_cnt = sum(1 for b in bend_switches if b.status == "FAIL")

    if not frames:
        overall_status = "UNVERIFIED"
    elif discontinuities or geometric_inconsistencies or unresolved_conditions:
        overall_status = "FAIL"
    else:
        overall_status = "PASS"

    return TraceAnalysisReport(
        overall_status=overall_status,
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
    sys.exit(0)


if __name__ == "__main__":
    main()

