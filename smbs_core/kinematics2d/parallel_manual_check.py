"""Parallel subprocess orchestrator for manual 3D bend-sequence checks (Stage D2).

Runs independent bend steps across worker subprocesses of FreeCAD Python,
ordered longest-first (-step_number) to minimize tail latency, while
preserving exact sequential stop-on-first-blocker semantics.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence


PASSING_STEP_STATUSES = frozenset({
    "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
    "CONDITIONAL",
})


def _run_worker(python_exe: str, job: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    timeout_s = float(job.get("timeout_s", 600.0))
    proc = subprocess.run(
        [python_exe, "-m", "smbs_core.kinematics2d.step_worker"],
        input=json.dumps(job, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=env,
    )
    if proc.returncode != 0:
        return {
            "step_number": job["step_number"],
            "bend_id": job["bend_id"],
            "status": "UNVERIFIED",
            "reason": f"worker_exit_{proc.returncode}:{(proc.stderr or '').strip()[:200]}",
            "completed_before_step": job.get("done", []),
        }
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return {
            "step_number": job["step_number"],
            "bend_id": job["bend_id"],
            "status": "UNVERIFIED",
            "reason": "worker_empty_stdout",
            "completed_before_step": job.get("done", []),
        }
    return json.loads(lines[-1])


def _file_sha256(path: str) -> str:
    import hashlib
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_sequence_parallel(
    python_exe: Optional[str],
    base_job: Dict[str, Any],
    order: Sequence[str],
    workers: int = 4,
    *,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    t0 = perf_counter()
    exe = python_exe or sys.executable
    enriched_base = dict(base_job)
    if enriched_base.get("use_cache", True):
        if not enriched_base.get("part_sha256") and enriched_base.get("part_path"):
            enriched_base["part_sha256"] = _file_sha256(str(enriched_base["part_path"]))
        if not enriched_base.get("punch_sha256") and enriched_base.get("punch_path"):
            enriched_base["punch_sha256"] = _file_sha256(str(enriched_base["punch_path"]))
        if not enriched_base.get("die_sha256") and enriched_base.get("die_path"):
            enriched_base["die_sha256"] = _file_sha256(str(enriched_base["die_path"]))
        if not enriched_base.get("code_sha256"):
            enriched_base["code_sha256"] = _file_sha256(__file__)
    rev_map = enriched_base.get("reverse_axis_by_bend") or {}
    jobs: List[Dict[str, Any]] = []
    for i, bend_id in enumerate(order):
        job = dict(enriched_base)
        job["bend_id"] = str(bend_id)
        job["done"] = [str(b) for b in order[:i]]
        job["step_number"] = i + 1
        job["reverse_axis"] = bool(rev_map.get(bend_id, base_job.get("reverse_axis", True)))
        jobs.append(job)

    # Longest-first scheduling: later steps have more bent flanges and cost more.
    queue = sorted(jobs, key=lambda j: -int(j["step_number"]))
    worker_count = max(1, min(int(workers), len(queue)))
    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        results = list(ex.map(lambda j: _run_worker(exe, j, env=run_env), queue))

    by_step = {int(r["step_number"]): r for r in results}
    steps: List[Dict[str, Any]] = []
    overall_status = "SAMPLED_3D_CLEAR_NOT_CONTINUOUS"
    overall_reason = "all_bends_sampled_clear"
    any_conditional = False
    stopped_early = False

    for n in range(1, len(order) + 1):
        r = by_step[n]
        steps.append(r)
        st = r.get("status")
        if st == "CONDITIONAL":
            any_conditional = True
        elif st not in PASSING_STEP_STATUSES:
            overall_status = str(st or "UNVERIFIED")
            overall_reason = (
                f"punch_or_die_collision_at:{r.get('bend_id')}"
                if st == "COLLISION"
                else str(r.get("reason") or "step_failed")
            )
            stopped_early = (n < len(order))
            break

    if overall_status == "SAMPLED_3D_CLEAR_NOT_CONTINUOUS" and any_conditional:
        overall_status = "CONDITIONAL"
        overall_reason = "conditional_end_zone_allowed"

    return {
        "schema_version": "smbs.parallel_manual_native.v1",
        "status": overall_status,
        "reason": overall_reason,
        "requested_bends": list(order),
        "checked_bends": len(steps),
        "stopped_early": stopped_early,
        "workers": worker_count,
        "wall_elapsed_s": round(perf_counter() - t0, 6),
        "steps": steps,
    }

