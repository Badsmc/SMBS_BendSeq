"""Subprocess worker for parallel single-bend native 3D validation (Stage D2).

Reads one JSON job from stdin, checks the disk step cache (Stage D1),
runs check_manual_3d_sequence for that single bend if needed, stores
cacheable verdicts, and writes the step JSON record to stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .step_result_cache import compute_step_cache_key, step_cache_get, step_cache_put


def run_job(job: dict) -> dict:
    part_path = str(job["part_path"])
    punch_path = str(job["punch_path"])
    die_path = str(job["die_path"])
    bend_id = str(job["bend_id"])
    done = list(job.get("done", ()))
    step_number = int(job.get("step_number", len(done) + 1))
    reverse_axis = bool(job.get("reverse_axis", True))
    match_active = bool(job.get("match_active_bend_length", True))
    allow_ez = bool(job.get("allow_end_zone_collision", True))
    ez_len = float(job.get("end_zone_length_mm", 20.0))
    cfg_dict = dict(job.get("adaptive_config") or {})
    part_sha = str(job.get("part_sha256", ""))
    punch_sha = str(job.get("punch_sha256", ""))
    die_sha = str(job.get("die_sha256", ""))
    code_sha = str(job.get("code_sha256", ""))

    cache_key = None
    if part_sha and punch_sha and die_sha and code_sha:
        cache_key = compute_step_cache_key(
            part_sha256=part_sha,
            punch_sha256=punch_sha,
            die_sha256=die_sha,
            config_dict=cfg_dict,
            bend_id=bend_id,
            completed_bends=done,
            reverse_axis=reverse_axis,
            allow_end_zone_collision=allow_ez,
            end_zone_length_mm=ez_len,
            match_active_bend_length=match_active,
            code_sha256=code_sha,
        )
        cached = step_cache_get(
            cache_key,
            step_number=step_number,
            completed_before_step=done,
        )
        if cached is not None:
            return cached

    import FreeCAD as App
    from freecad_smbs.commands.pressbrake_2d import build_part_session
    from .tooling_25d import ToolLibrary25D, ToolingSetup2D, ToolKind, ValidationStatus
    from .motion_trajectory_3d import AdaptiveSamplingConfig
    from .manual_physical_validation import check_manual_3d_sequence
    from .developer_debug_log import _jsonable

    cfg = AdaptiveSamplingConfig(**cfg_dict) if cfg_dict else AdaptiveSamplingConfig(
        allow_end_zone_collision=allow_ez,
        end_zone_length_mm=ez_len,
    )
    doc = App.openDocument(str(Path(part_path).expanduser().resolve(strict=True)))
    try:
        unfold = doc.getObject("Body_Unfold")
        session = build_part_session(doc, [unfold] if unfold is not None else [])
        rest = [b for b in session.bend_order if b not in set(done) and b != bend_id]
        session.set_bend_order([bend_id] + list(done) + rest)
        first_bend = session.ordered_bends[0]
        library = ToolLibrary25D(Path(punch_path).parent.parent)
        punch = library.load_tool(Path(punch_path).stem, ToolKind.PUNCH)
        die = library.load_tool(Path(die_path).stem, ToolKind.DIE)
        setup = ToolingSetup2D()
        setup.select_punch(punch)
        setup.select_die(die)
        setup.set_match_active_bend_length(match_active)
        setup.allow_end_zone_collision = allow_ez
        setup.end_zone_length_mm = ez_len
        first_bend = session.fold_state.bends[bend_id]
        setup.align_to_cross_section(
            session.cross_section(),
            bend_zone=first_bend.bend_zone,
            thickness_mm=float(first_bend.bend_zone.thickness_mm),
        )
        res = check_manual_3d_sequence(
            session,
            setup,
            [bend_id],
            match_active_bend_length=match_active,
            allow_end_zone_collision=allow_ez,
            end_zone_length_mm=ez_len,
            reverse_axis_by_bend={bend_id: reverse_axis},
            config=cfg,
            include_benchmark_metrics=bool(job.get("include_benchmark_metrics", True)),
            pre_completed_bends=done,
        )
        steps = res.get("steps") or []
        if not steps:
            return {
                "step_number": step_number,
                "bend_id": bend_id,
                "status": res.get("status", "UNVERIFIED"),
                "reason": res.get("reason", "no_step_returned"),
                "completed_before_step": done,
            }
        step_rec = _jsonable(steps[0])
        step_rec["step_number"] = step_number
        step_rec["completed_before_step"] = done
        step_rec["cache_hit"] = False
        if cache_key is not None:
            step_cache_put(cache_key, step_rec)
        return step_rec
    finally:
        App.closeDocument(doc.Name)


def main() -> None:
    job = json.loads(sys.stdin.read())
    out = run_job(job)
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

