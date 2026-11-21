# FAST shortlist: bounded advisory 2D screening (experimental)

Branch: `fix/fast-axis-soft-penalty-20261120`

## Intent

Keep the existing ~1 second FAST Beam Search independent of native geometry.
After the FAST result dialog opens, run a **separate** advisory Stage A/B
screening over the returned (up to five) candidate sequences, with a **3 s
cooperative budget**. Prefer sampled plans with fewer 2D tool-interference
indications, without claiming real-machine or continuous-3D feasibility.

Sources: `fast_bfm_beam_search.py` (generator),
`fast_shortlist_screening.py` (advisory 2D post-processor),
`fast_side_seeds.py` (real unfolded perimeter directions),
`fast_multiside_feedback.py` (forced per-side feedback search),
`fast_collision_gate.py` (strict exact-state sampled punch/die
material-penetration filtering),
`preliminary_screening.py` (existing Stage A/B geometry),
`freecad_smbs/commands/pressbrake_2d.py` (Qt integration).

## Execution

1. FAST runs unchanged with `limit_seconds=1.0`.
2. Its original candidates are visible in a nonblocking Qt dialog.
3. `QtCore.QTimer.singleShot(0, ...)` starts the separate 2D post-processor
   *after* the dialog opens. This defers work but is **not a background
   thread**. FreeCAD's event loop may be unresponsive while a probe runs.
4. Stage A validates selected tooling inputs/length contracts.
5. Stage B samples actual panel sections and selected punch/die profiles at
   approach, 0/50/100% bending, bottom and retract phases. A synthetic 2D
   rear-gauge polygon is checked only when non-CAD gauge mode supports it.
   Full CAD stop checks, side tool spans and clamp/frame checks are NOT run.
6. Cache key: `(completed bend-angle map, next bend ID, 0/180 orientation)`.
   Do not cache based on last bend alone or omit 180 orientation.
   Cache only sampled-clear and observed 2D obstruction statuses.
   `BUDGET_EXCEEDED` and ambiguous/setup errors are NEVER cached:
   a repeated pass must retry unfinished transitions.
7. The Stage B report captures the first suspect transition as
   `(completed_operation_mask, operation_id, reverse_axis)`. A sampled
   interference produces a SOFT +1500 transition penalty. No physical bend
   ID is globally forbidden; the same bend can pass in another prefix.
8. Extract the **authoritative unfolded external CAD wire** (not panel
   bounding boxes, individual panels or inner holes). PanelBuilder preserves
   `outline_edge_polylines_xy`, with one polyline per actual CAD Edge.
   Every authored exterior edge is retained, including short chamfers,
   stair-step notches, and separate collinear edges. Curved CAD edges are
   sampled into local tangent sectors at roughly 12-degree turns; a curve
   has no finite number of mathematical sides. There is NO fixed 4/6/24
   side limit. Four-edge outlines can be labelled LEFT/RIGHT/TOP/BOTTOM;
   any other exterior contour is SIDE_1..SIDE_N in its CAD order. Missing
   original CAD metadata falls back to conservative collinear-only
   simplification. If no valid outline exists, report
   OUTER_CONTOUR_UNAVAILABLE.
9. Enumerate every real exterior side and schedule a forced first-bend
   search with the corresponding operator-facing 0/180 loading orientation.
   The 2.5 s recovery budget is SHARED across all sides. If two CAD edges
   create exactly the same first-bend/orientation constraints, their
   equivalent Beam Search is reused; both edges remain in diagnostics.
   Every side receives a `side_findings` record, including sides omitted
   because of time budget. A side lacking a parallel bend is an explicitly
   approximate setup, not claimed physically accessible. Manual pins
   remain authoritative. If many unique sides exceed the budget, **not
   every side can be fully searched**; the interface now reports detected,
   executed, reused and skipped counts explicitly.
10. Append distinct forced-side candidates to the FAST list and
    screen the expanded set again with Stage B. Reuse the same per-dialog
    transition cache to avoid recomputing the original candidates; screen
    NEW candidates first so they are not all starved by old paths.
11. From Stage B's selected-tool reports, preserve the real
    `tool=PUNCH/DIE`, phase, rigid sheet component, and material-overlap
    classification. **Only** a sampled rigid nonactive-panel crossing with
    a selected punch/die during APPROACH, BENDING or BOTTOM can become a
    coarse search exclusion. A raised PUNCH that already deeply overlaps
    the active rigid flange during APPROACH also blocks that loading
    pose: it is not intended contact with the bend. Active flange/tool
    contact during BENDING is not sufficient for hard gating.
    TOUCH, uncertain contact, RETRACT, the active bend zone, missing
    metadata, CAD gauge, timeouts and mere BBoxes are NEVER hard
    exclusions.
12. Rerun the full Beam Search for up to 1.5 s with a set of exact
    `(already-completed operation mask, next operation ID, 0/180)`
    forbidden transitions. These are **search constraints only**, NOT
    a declaration that the bend is impossible. Unknown alternatives
    remain searchable; only previously observed gross 2D penetration
    poses are removed. Up to eight fresh candidates are added and
    sampled by Stage B with priority.
13. Run a second bounded 1.5 s recovery Beam Search using original
    2D penalties, the new forbidden transitions and forced opposite
    0/180 orientations of troublesome bends. Include coordinated
    two- and three-bend reversal experiments for the most frequent
    different obstruction IDs, with the SAME per-round budget.
    One-flip-only reruns can move a failure from B06 to B04 without
    improving the checked prefix; coordinated repairs target this.
    Manual locks remain authoritative. The repeat button supports
    two more bounded recovery rounds. No combination is 3D certified.
14. The result table hides paths showing the strict coarse-2D
    obstruction by default. They are not deleted; the operator can
    check "Показывать исключённые грубой 2D-проверкой" for diagnosis.
    If the provisional filter hides every candidate, the dialog now
    displays a clear "0 вариантов" warning instead of an unexplained
    empty table. No option is called verified or physically impossible:
    review excluded paths or change tooling and repeat. The scorer
    never promotes a missed check to a successful sampled pass.
15. The results dialog now uses `QTableWidget`, with sortable columns
    **№ | 2D | Вес | 2D гибов | Старт | 1-й гиб | 180° | Ось |
    2D штраф | Порядок | Причина**. Floating-point weights are sorted
    NUMERICALLY despite display with decimal comma. `2D гибов` counts
    only the consecutive operations with sampled tool-section clearance,
    NOT verified 3D bends. Each row stores a stable internal candidate
    index so sorting never changes which sequence will be applied.
16. SAMPLED clear uses ✅, incomplete/untested uses ⚠️, and possible
    collision uses ❌. These are **advisory 2D statuses only**. Red
    does not prove the entire sequence impossible, green never certifies
    machine-safe bending. Results rerank but are never automatically
    deleted or applied. All physical 3D clearance remains `UNVERIFIED`.

## Key statuses

- `SAMPLED_2D_TOOLS_GAUGE_CLEAR`: completed a sampled sectional preview
  of tools and *synthetic* rear gauge. Not a true machine clearance pass.
- `SAMPLED_2D_TOOLS_ONLY`: sampled tool sections without validated gauge.
- `POTENTIAL_2D_INTERFERENCE`: sampled overlapping sections need 3D review.
- `STATIC_TOOLING_BLOCK`: Stage A input/length issue, NOT an order proof.
- `PARTIAL_UNVERIFIED`: unknown, ambiguous, incomplete or probe exception.
- `NO_TOOLING`: select punch and die; no geometry verdict was made.
- `BUDGET_EXCEEDED`: no verdict when the cooperative budget elapsed.
- `STAGED_UNSUPPORTED`: PREBEND / FINISH use partial target angles that
  the existing Stage B cannot replay; never fake a normal full-angle pass.

The 2D post-processor never certifies an executable bend sequence. Native
3D/OCC verification remains necessary before any production use.

## Timing and host safety

- FAST `limit_seconds=1.0` is unaffected by post-processing.
  The dialog and `FAST_POSTPROCESS_SUMMARY` log now report cumulative
  additional elapsed time and estimated end-to-end duration separately
  from the final Stage B sample pass.
- Initial 2D, expanded-side 2D, and optional feedback-expanded 2D
  passes each have separate *cooperative* budgets of 3 s. Stage B
  tests the deadline between samples but individual geometry calls
  may overrun. Only completed, meaningful geometry findings are cached;
  budget failures are retried on later passes.
- A new full collision-gated Beam Search uses a 1.5 s budget before the
  1.5 s transition/orientation feedback round.
- Additional transition-feedback Beam Search uses a 1.5 s shared budget
  per round, up to three rounds within the same open result dialog
  (one automatic and two on manual 'check again' actions). The sum of
  independent budgets is NOT a strict wall-clock upper bound.
- All-side Beam Search `budget_seconds=2.5` is shared across every
  detected exterior side. If a side times out, it is reported rather than
  silently omitted. The first 1 s FAST stage remains unchanged.
- Forced starts are **not** proof of physically reaching that edge:
  actual stop contact, operator loading, interference outside a cutting
  section and CAD tooling/clamps remain for full 3D verification.
- Stage 2 and side forcing run on the FreeCAD GUI event loop and may
  temporarily block the UI. They are not cancellable worker tasks.
  End-to-end time is not guaranteed to be bounded by the sum of budgets.
- A bounded, **nonblocking** worker would need an isolated immutable
  geometry snapshot and a separate worker process; do not move mutable
  FreeCAD objects or Qt widgets into worker threads.
- The post-processor saves/restores `FoldState.angles` and
  `FoldState.transforms` in `finally`; Stage B restores tooling internals.
- The UI only reorders labels. It does not apply a new sequence until the
  operator explicitly accepts a candidate.

## FreeCAD acceptance check

1. Restart FreeCAD with this exact branch installed.
2. Load `2-1-3-4-(ok).FCStd`, select the relevant SheetMetal Unfold.
3. Select punch and die, reset playback, press FAST.
4. Confirm FAST results appear first. After 2D evaluation the dialog
   must show more candidates labelled by external side whenever such
   sequences are found. Confirm a rectangular contour detects four
   external CAD edges, a hexagon six, and a stepped/chamfered contour
   the exact number of real edges (plus adaptive curved sectors).
5. Confirm that side candidates have distinct leading bends where
   geometry permits, and operator-facing 0/180 starts.
6. Sort all table columns by clicking their headings, especially
   numeric "Вес" (23,55 must sort ahead of 1593,00) and "2D гибов"
   (e.g. 7/10 must sort ahead of 2/10). Confirm chosen table row
   still applies the correct sequence, even after sorting by cost,
   status and source edge.
7. Trigger "Проверить ещё раз / найти новые варианты" to retry timed-out
   2D checks and (while rounds remain) rerun feedback with opposite
   orientations. Explicit manual 0/180 pins must never be overridden.
8. Confirm operation table, simulation and document windows remain intact.
9. Inspect `SMBS_debug_log.json` for `FAST_SEARCH_RESULT`,
   `FAST_SHORTLIST_2D_SCREEN_INITIAL`, `FAST_FORCED_ALL_SIDES_RESULT`,
   `FAST_FEEDBACK_RECOVERY_RESULT`, `FAST_SHORTLIST_2D_SCREEN`,
   `FAST_POSTPROCESS_SUMMARY`, `FAST_RESULT_DIALOG_FINISHED`,
   and `FAST_CANDIDATE_APPLIED`.
10. Record `side_findings` including each `status`, per-side
   `first_orientation_by_operation`, `returned_candidates`,
   `new_candidates`, `elapsed_seconds`, `actual_probes`, `cache_hits`,
   each `first_issue`, and any `BUDGET_EXCEEDED`. Compare against
   observed native 3D/tooling contacts.

Pure Python regression:
`python -m unittest tests/test_fast_shortlist_screening.py tests/test_fast_collision_gate.py tests/test_fast_multiside_feedback.py tests/test_fast_search_gui_contract.py`

## Next steps, *not* implemented yet

- Validate PREBEND and FINISH partial-angle paths with a stage-aware
  sweep; Stage B's current sweep assumes the full bend final angle.
- Compare actual 2D gauge contacts against the CAD contact model, accounting
  for virtual tooling and station alternatives.
- Feedback recovery and strict sampled 2D gate are now added after
  the side passes, but even a strict 2D material crossing can be a
  false positive due to assumed loading pose and cutting-plane choice.
  Tool sections outside that plane, side tool ends, real clamp/frame,
  backgauge contact, and continuous ram motion remain unverified.
  The strict gate must never be treated as independent 3D validation.
- Measure real FreeCAD timings; the original 1-second FAST is unchanged,
  but added 2D passes and collision-aware reruns increase total dialog time.
- Validate the full machine pose and gauge contact for each starting
  exterior edge; this release uses authoritative unfolded geometry as
  a search seed, not a certified global loading placement.
- Run independent multi-section mesh and full physical checks on selected
  candidates; do not turn sampled-clear into a safety certificate.
