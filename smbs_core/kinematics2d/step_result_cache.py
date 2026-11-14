"""Disk cache for verified single-bend 3D native validation results (Stage D1).

Cache keys include part/tool SHA-256, adaptive config, active bend,
unordered set of completed prior bends, orientation/end-zone policy,
and the composite source code SHA-256 so any code edit invalidates
stale entries automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


CACHEABLE_STATUSES = frozenset({
    "SAMPLED_3D_CLEAR_NOT_CONTINUOUS",
    "CONDITIONAL",
    "COLLISION",
})


def default_step_cache_dir() -> Path:
    override = os.environ.get("SMBS_STEP_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "FreeCAD" / "smbs_cache"


def compute_step_cache_key(
    *,
    part_sha256: str,
    punch_sha256: str,
    die_sha256: str,
    config_dict: Dict[str, Any],
    bend_id: str,
    completed_bends: Sequence[str],
    reverse_axis: bool,
    allow_end_zone_collision: bool,
    end_zone_length_mm: float,
    match_active_bend_length: bool,
    code_sha256: str,
) -> str:
    payload = json.dumps(
        [
            str(part_sha256),
            str(punch_sha256),
            str(die_sha256),
            config_dict,
            str(bend_id),
            sorted(str(b) for b in completed_bends),
            bool(reverse_axis),
            bool(allow_end_zone_collision),
            round(float(end_zone_length_mm), 6),
            bool(match_active_bend_length),
            str(code_sha256),
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def step_cache_get(
    key: str,
    *,
    step_number: int,
    completed_before_step: Sequence[str],
    cache_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    if os.environ.get("SMBS_STEP_CACHE", "1") != "1":
        return None
    root = cache_dir or default_step_cache_dir()
    path = root / f"{key}.json"
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(record, dict) or record.get("status") not in CACHEABLE_STATUSES:
        return None
    out = dict(record)
    out["step_number"] = int(step_number)
    out["completed_before_step"] = list(completed_before_step)
    out["cache_hit"] = True
    return out


def step_cache_put(
    key: str,
    record: Dict[str, Any],
    *,
    cache_dir: Optional[Path] = None,
) -> bool:
    if os.environ.get("SMBS_STEP_CACHE", "1") != "1":
        return False
    if not isinstance(record, dict) or record.get("status") not in CACHEABLE_STATUSES:
        return False
    root = cache_dir or default_step_cache_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{key}.json"
        tmp = root / f"{key}.json.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False

