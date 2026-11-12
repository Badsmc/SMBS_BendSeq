# SMBS developer debug journal — Desktop, always on

## Purpose

For local debugging of FreeCAD 1.1.x PressBrakeSim 2.5D. This is the **one
always-on diagnostic file**:

- Windows (normal or OneDrive redirected Desktop):
  `<actual Windows Desktop>\\SMBS_debug_log.json`
- macOS/Linux: `~/Desktop/SMBS_debug_log.json`

SMBS resolves the Windows Desktop from the registry User Shell Folders
key first; OneDrive and home/Desktop are fallbacks. A selectable copyable
path is shown under **SMBS → Настройки → Отладка**.

**No record/export action is required.** The journal is created the
first time the addon runs, updated after each significant event, and
**appended across FreeCAD restarts** with a new `session_id`. One file
contains both user actions and native collision evidence. No network
transmission occurs. The existing optional JSONL/summary manual trace
remains a separate, opt-in feature; it is *not needed* for this log.

## What is recorded

A top-level JSON object with `schema_version` and ordered `events[]`.
Every event has `sequence`, UTC `utc`, `monotonic_ns`, `session_id`,
`event` and `data`.

**GUI actions / sessions**
- `SMBS_SESSION_STARTED`: OS, Python, process and absolute log path
- `SMBS_RUNTIME_READY`: actual workbench module path, Git branch/commit,
  configured initial ram clearance
- `TASKS_OPENED`, `TASKS_SWITCH_SECTION`, `TASKS_CLOSE_DETACH`:
  native FreeCAD task panel ownership and reopen sequence
- `OPERATOR_UI_ACTION`: clicked buttons, checked controls, changed combo
  selections, numerical settings, released sliders and view tabs; includes
  active bend, entire bend order, current angles, selected punch/die,
  tool-length and end-zone options
- `PART_LOAD_REQUEST`, `PART_LOAD_OUTCOME`, `PART_GEOMETRY_LOADED`:
  selected FreeCAD object, FCStd path, panel polygons, bend axes,
  thickness, angle, inner radius, bend allowance and fixed panel
- `PREVIEW_GEOMETRY_FRAME`: periodically sampled rendered 2.5D panel
  coordinates, punch/die transforms, press angle and Z, status/phase,
  source unfold reference. Captured **up to 5 Hz**, not 40 fps
- `FREECAD_WARNING`, `FREECAD_ERROR` and existing SMBS trace events:
  FreeCAD/OCC errors with full exception traceback when available

**Manual 2D/3D checks**
- `MANUAL_VERIFY_REQUESTED`: the exact selected bend sequence and tool
  settings at the moment Verify was pressed
- `MANUAL_STAGE_AB_PLANAR_RESULT`: full Stage A/B replay response,
  rejections and diagnostic findings
- `MANUAL_NATIVE_CHECK_STARTED`: selected physical FCStd punch/die,
  virtual/real length, conditional end zones and D6 adaptive config
- `MANUAL_NATIVE_BEND_STARTED`: current bend, original axis and signed
  history of all previously completed bends
- `MANUAL_NATIVE_BEND_TRAJECTORY`: **all OCC-sampled states of every D6
  phase** (APPROACH, CONTACT, BENDING, BOTTOM, RETRACT): phase, `path_u`,
  signed angle, ram Z, support Z, punch/die overlap volumes, distances,
  collision categories, unresolved intervals, discontinuities and raw
  validator diagnostics, without truncating the trace to 650 characters
- `PUNCH_DIE_NATIVE_RAM_SAMPLE`: separate tool-vs-tool results at every
  tested ram height, actual overlap volume, distance and machine XYZ
  intersection bbox, if available
- `MANUAL_NATIVE_BEND_RESULT`: per-bend verdict, exact first rejected
  phase/tool/angle/ram Z, RETRACT data and runtime
- `MANUAL_NATIVE_CHECK_FINISHED`, `MANUAL_VERIFY_FINAL_RESULT`: full
  selected-order verdict including the real reason for stopping early
- `FCSTD_MANUAL_DIAGNOSTICS_STAGED/SAVED/PENDING_FIRST_SAVE`: whether the
  report also reached `SMBS_Data/SessionJSON` inside the source FCStd

## Finding a collision, e.g. B07 → B03

1. Run SMBS Manual → **Проверить** to reproduce the issue. No extra
   debug controls need to be enabled.
2. Open the Desktop `SMBS_debug_log.json` and search for
   `"event":"MANUAL_NATIVE_BEND_TRAJECTORY"` plus `"bend_id":"B03"`.
3. Inspect `data.native_d6_samples.phases[]` to locate the first phase
   whose `evidence_status` is not `SAMPLED_INTERVAL_CONVERGED` or a
   recognised conditional end-zone. Inspect its `samples[]` for
   `collision_status`, `punch.status`, `die.status`, the individual
   `overlap_volume_mm3`, and `ram_z_mm` / `angle_deg`.
4. Where available, `punch.overlap_bbox_xyz_mm` and
   `die.overlap_bbox_xyz_mm` are the **actual BRep common-solid
   intersection bounds in machine X/Y/Z mm**, not an estimated location
   from overlapping original tool bounding boxes. If the kernel
   provides no valid common bbox, the value stays `null`; do not invent
   a collision position.
5. For the quick answer, find the later
   `"event":"MANUAL_NATIVE_BEND_RESULT"` and inspect
   `data.bend.first_blocker`. A failed BENDING/BOTTOM will show its
   phase *before* long APPROACH timing lines.
6. If a collision aborts the calculation before RETRACT,
   `data.bend.retract.status` will be `NOT_EVALUATED`.
   This is not a proven retract collision.

## Robustness / limitations

- Appending an event modifies only the closing JSON suffix, not the
  previous megabytes. A completed append leaves a valid `.json`.
  If FreeCAD or Windows crashes **mid-write**, the next SMBS startup
  salvages complete records and marks
  `RECOVERED_INTERRUPTED_JOURNAL`. The unfinished final event cannot
  always be recovered.
- Diagnostic writes are local and best effort; disk-full or read-only
  Desktop must **never** abort physical calculation. The journal can
  grow large during extended CAD sessions. Manually archive or delete
  it *when FreeCAD is closed* if you need a fresh file.
- Native samples are the actual positions the D6 adaptive validator
  evaluated. The log does **not** contain complete binary BRep
  solids, continuous uninspected trajectory portions or an assertion
  that a press brake is safe for production.
- The file contains names of parts, local file paths and selected
  geometry. Review its contents before sharing outside your team.
- The live FreeCAD interface and actual OpenCASCADE behavior on the
  user's Windows setup still need interactive acceptance. Python mocks
  and GitHub Actions cannot establish native UI correctness.
