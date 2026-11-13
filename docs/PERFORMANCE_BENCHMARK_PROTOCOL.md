# SMBS performance benchmark protocol (Phase A)

Version 1.0, 2026-11-13. Do not claim a speedup without matched native FreeCAD runs.

## A. Reference workload

- Test part: actual SheetMetal `2-1-3-4-(ok).FCStd` (record SHA-256, don't change the source). If missing, use the actual attached 10-bend reference and record its exact path/sha.
- Tooling: same chosen real FCStd punch AP1017 and die AT3015 V12; record SHA-256 for each file and `tool_id`. Never substitute source example puancon/matrica without reporting change.
- Manual order: `B07 B03 B05 B01 B08 B04 B09 B10 B06 B02`. Record whether prefix/all bends requested and which mode was selected.
- Record exact `reverse_axis_by_bend` selection (typically 180° for all selected bends), Conditional setting and end-zone length (historically 10 mm), source length versus virtual matching setting and tool section offsets.
- Record `AdaptiveSamplingConfig` (especially max phase time, max states/phase, arc_segments, near/volume/contact tolerances).
- Do not mix benchmark outputs from different configuration, geometry, FreeCAD version or software commit.

## B. Required environment metadata

- Host OS/architecture and version (Windows 11 or macOS), FreeCAD version, bundled Python and OpenCASCADE/OCC versions if available.
- CPU model, physical cores/logical threads, RAM, worker count (initially 1), whether AC-powered or throttled.
- Installed runtime module path, checkout branch and commit SHA. The GUI may log branch/commit UNKNOWN for a ZIP installation; record the source directory manually. Never assume GitHub HEAD was installed.
- Tool and model file SHA-256, exact date, journal filename and session ID.

SHA-256 example with standard Python:
`python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <filename.FCStd>`

## B2. One-command local native runner (real FreeCAD, optional)

The branch includes `tools/run_native_benchmark.py`. It uses the **actual**
SheetMetal Unfold, FreeCAD Part OCC and Manual D6 service. This is NOT a
mock test. From FreeCAD 1.1.x's Python console, after opening the installed
development checkout and ensuring the SheetMetal workbench is installed:

```python
import sys
sys.path.insert(0, r"C:\\path\\to\\SMBS_BendSeq")
from tools.run_native_benchmark import run
result = run()
```

The script defaults to the repository reference file
`demo/parts/2-1-3-4-(ok).FCStd`, the real
`PUNCH_AP_1017_88°_H104,65_R0,8` and
`T-DIE_AT_3015_88°_V12_H80` FCStd tool IDs, the ten-bend Manual order,
180° reversal, virtual active-bend length and 10-mm Conditional end zones.
It opens the source document hidden and **never saves it**; produces separate
`~/Desktop/SMBS_native_benchmark.json` with hashes and verdicts and appends
normal Desktop `SMBS_debug_log.json` profiling events. Use
`run(part=..., output=..., unfold_name=...)` only for genuinely different
test inputs and record all changed parameters.

Requirements: run in FreeCAD Python/FreeCADCmd with FreeCAD 1.1.x and
`SheetMetalNewUnfolder` installed; the shell's system Python is NOT suitable.
This entry point is headless-tested for imports/arguments, but its actual
native completion requires local FreeCAD acceptance and may stop at a proven
collision or at an unresolved step. It is NOT a manufacturing approval.

## C. Measurement protocol

1. Disable unrelated background CPU-heavy workloads where feasible; use the same machine power mode across runs.
2. Keep existing original `SMBS_debug_log.json` as the only write target of SMBS. Do not edit its contents.
3. Warmup: open FreeCAD, confirm correct installed module and selected input, run explicit Manual **Проверить 3D** once, collect event run ID and outcome; note if geometry invalid or incomplete.
4. For a cold benchmark, restart FreeCAD and run identical check. Repeat 3 times where practical.
5. For warm runs, preserve the same FreeCAD process and rerun identical check 3 times; note user-level state/caches if any. Do NOT confuse per-validator cache with cross-run cache.
6. Record wall times from debug journal; collect resource information (peak process RAM, approximate CPU usage); if no stable resource probe is available, write `NOT_MEASURED`.
7. Compare a baseline and optimized branch only with exactly matched model/tools/configuration and warm/cold classification.
8. Confirm physical results: per-bend statuses, first hit tool, bend, phase, angle / onset bracket, overlap volume and Conditional verdict. Unexpected mismatch means FAIL even when fast.

## D. Analyzer CLI (read-only)

From repository root with Python 3.11+:

```shell
python -m smbs_core.kinematics2d.performance_report "PATH/TO/SMBS_debug_log.json"
python -m smbs_core.kinematics2d.performance_report "PATH/TO/SMBS_debug_log.json" --json
python -m smbs_core.kinematics2d.performance_report "PATH/TO/SMBS_debug_log.json" --json --output "PATH/TO/perf_summary.json"
python -m smbs_core.kinematics2d.performance_report "PATH/TO/baseline_log.json" --compare "PATH/TO/optimized_log.json" --json --output "PATH/TO/perf_comparison.json"
```

The analyzer MUST never alter the original journal or call FreeCAD. The optional `--compare` mode reports a wall-time ratio ONLY when requested bend order, selected tool/configuration, per-bend verdicts, D6 evidence and first recorded tool/phase/angle agree. It is **provisional**: source FCStd/tool SHA hashes and live OCC acceptance still need independent validation; if they are not verified, never claim a qualified speedup. Older logs can lack new counters; show `null`/NOT_RECORDED instead of 0 when fields were not recorded. Support multiple Manual native runs in a single journal, including incomplete runs. Split by `MANUAL_NATIVE_CHECK_STARTED` / `MANUAL_NATIVE_CHECK_FINISHED` and correlate `MANUAL_NATIVE_BEND_TRAJECTORY`, `MANUAL_NATIVE_BEND_RESULT` and optional `PERF_NATIVE_BEND_KERNEL`. Do not sum phase + aggregate query counts together (double counting).

## E. Report schema

```json
{
  "schema_version": "smbs.performance_baseline.v1",
  "source_journal": "SMBS_debug_log.json",
  "run_count": 1,
  "runs": [
    {
      "start_utc": null,
      "finish_utc": null,
      "final_status": null,
      "completed": false,
      "requested_order": [],
      "configuration": {},
      "elapsed_s": null,
      "bends": [
        {
          "bend_id": "B07",
          "status": null,
          "elapsed_s": null,
          "native_d6": {
            "total_states": null,
            "total_solid_reconstructions": null,
            "total_common_queries": null
          },
          "phases": []
        }
      ]
    }
  ]
}
```

Additional metrics are allowed. `null` means not observed, never assumed safe/zero.

## F. Exit criteria

Gate A0: docs / agent handoff. Gate A1: pure Python analyzer with tests on synthetic journal. Gate A2: native serialization plus Part.common/distToShape measurement instrumentation, no physics changes. Gate A3: actual FreeCAD JSON and comparison tables; baseline SHA/hardware/config recorded. **A0–A2 do not constitute a measured speedup**.

Every optimization PR records: baseline median cold/warm, optimized median, ratio baseline/optimized, sample sizes, call counts and cache rates, max RAM, unchanged verdicts and outstanding evidence gaps. If native runs are unavailable, mark `HEADLESS_TESTED / NOT_BENCHMARKED`, not QUALIFIED.
