# LOG15 — Exact same-object machine sheet reuse across ram-only poses

2026-11-14; active source branch: `experiment/component-collision-oracle`.

## Evidence and hypothesis

Previous Windows run at 2026-11-13 14:45: wall **1051.5764s** for the ten-bend Manual D6 path (297 full-sheet unions). Actual native Mac run with K=0.40 and 20mm Conditional relief (different geometry/settings) took **1712.3593s**. Its aggregate measured regions include:
- `pair_geometry_sanity` **325.064s** (repeated `isValid/isClosed/Volume` of sheet and tools).
- `sheet_qualification` **199.777s**.
- `sheet_union` **152.282s**.
- `bend_zone_build` **134.339s**.
- `machine_workpiece_copy` is recorded separately and must not be counted as proof that all sanity work is avoidable.

For the FIRST Mac bend B07, the real complete sample benchmark took approximately **74.73618s** with **34 sheet unions** and **8.77130s** `pair_geometry_sanity`.

Problem: on BOTTOM and RETRACT the same FoldState angle tuple and machine sheet placement can be reused while ONLY the ram Z of the punch changes. The existing full-sheet cache avoids union but the validator still copies the entire joined native BRep and validates the resulting shape again at each such ram position.

Hypothesis: retaining the EXACT SAME machine-frame BRep Python object and ONE prior `check_solid_validity` result can eliminate redundant sheet copies and workpiece validity checks when BOTH the full sheet source object identity and all rigid pose coefficients are unchanged. Punch/die exact Common, exact distances, Conditional classification, union/qualification for new fold states and adaptive sampling all remain unchanged.

## Implementation — opt-in, default OFF

- `native_collision_3d.validate_workpiece_tooling_pose` accepts optional private `_validated_workpiece_shape=(exact_shape_object, (bool, reason))`, but only if `exact_shape_object is workpiece_machine_shape` and the proof tuple is well formed. Otherwise the original `check_solid_validity` executes normally.
- `AdaptiveTrajectoryValidator3D` keeps ONE machine-frame sheet only: `(joined_source_shape_object, (solid_cache_key, exact_rotation_matrix, exact_translation_xyz), machine_shape, validity)`. Object identity, key and placement MUST all agree; changing ram only is permitted. Any new joined source object or changed placement misses and generates/validates a fresh copy.
- Source invalidation, config/tool changes, and `clear_caches()` discard the retained machine BRep. Invalid sheet validity results cannot be stored as reusable proofs. Native Part shapes are never sent to another process; no retained source geometry is mutated by this new code.
- Environment opt in: `SMBS_MACHINE_POSE_REUSE=1`, otherwise baseline `=0`. No GUI/user settings, FCStd, selected machine/tool assembly, collision tolerances, contact policy or automatic search algorithm are changed.
- Per-bend D6 reports and the existing Desktop journal now contain `total_machine_pose_reuse_hits` and `total_machine_pose_reuse_misses`. Benchmark JSON also records the effective opt-in switch. `performance_report.py` extracts the counters and reports the optimization toggle separately from physical configuration, so matched ON/OFF runs can be compared fairly.

## Short native A/B benchmark: run only reference FIRST bend, not all ten

HEADLESS tests verify shape identity cache hit, invalidation on machine pose change, disabled baseline, pair oracle identical-object contract, and diagnostic counters. Those do **not** constitute a native OCC benchmark.

Update ONE intended FreeCAD Mod checkout of this branch and fully restart FreeCAD (imported Python modules remain stale after a ZIP overlay). Verify:
```python
import smbs_core.kinematics2d.motion_trajectory_3d as native
print(native.__file__)
from tools.run_native_benchmark import run
```

For the previously tested Mac source FCStd, selected-tool version and K-factor stay unchanged. Run the SAME reference first bend B07 in each mode, in FreeCAD Python console:

```python
from pathlib import Path
import os
from tools.run_native_benchmark import run
home = Path.home() / "Desktop"
part = home / "Новая папка" / "2-1-3-4-(ok).FCStd"
os.environ["SMBS_COMPONENT_REJECT"] = "0"
os.environ["SMBS_COMPONENT_SHADOW_SAMPLES"] = "0"
os.environ["SMBS_MACHINE_POSE_REUSE"] = "0"
baseline = run(part=part, prefix_bends=1,
               end_zone_length_mm=20.0,
               output=home / "SMBS_B07_pose_baseline.json")
os.environ["SMBS_MACHINE_POSE_REUSE"] = "1"
candidate = run(part=part, prefix_bends=1,
                end_zone_length_mm=20.0,
                output=home / "SMBS_B07_pose_reuse.json")
print(baseline["result"]["steps"][0]["status"], baseline["wall_elapsed_s"])
print(candidate["result"]["steps"][0]["status"], candidate["wall_elapsed_s"])
```

The whole-prefix result may be **UNVERIFIED** because ONLY B07 was requested; do not call that a safe ten-bend sequence. Compare the STEP B07 native status, sample counts, Common and distance counts, union and qualification counts, first-blocker evidence, and measured wall seconds. New result JSON files give original part/tool SHA and runtime/OS provenance. The existing Desktop `SMBS_debug_log.json` contains all D6 per-bend events and new hit/miss counters.

**Acceptance:**
- No new `CLEAR`, no changes to real blocker or Conditional statuses, and exact quantities unchanged within normal native numerical tolerances. A mismatch invalidates the experiment.
- `total_machine_pose_reuse_hits > 0` in ON mode and exactly 0 in OFF mode; if 0 on both, the idea is ineffective on that prefix (do not claim speedup).
- `machine_workpiece_copy` region call count decreases with hit count; `pair_geometry_sanity` time should decrease if this BRep validation was an expensive repeated operation, but **this remains a hypothesis until measured**.
- Full 10-bend A/B and an independent exact native final check are needed before default enablement or production qualification. Do not change `main` or `smbs`.

## Limitations

This optimization cannot remove the **297 repeated full-sheet unions** and **297 sheet qualifications** on novel fold states and cannot by itself achieve 1–3 minutes for ten bends. It is a bounded Phase-B exact work reuse step. For the larger goal, first qualify the existing opt-in Conditional-aware positive-reject component pre-fuse filter (`SMBS_COMPONENT_REJECT=1`) with identical Mac/Windows runtime and tooling; then develop a genuinely safe incremental exact sheet-topology qualification strategy. All non-proven conditions must fall back to the full OpenCASCADE validator.
