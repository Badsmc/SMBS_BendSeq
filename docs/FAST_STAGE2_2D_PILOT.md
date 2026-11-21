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
7. The Stage B report captures the first suspect transition as
   `(completed_operation_mask, operation_id, reverse_axis)`. A sampled
   interference produces a SOFT +1500 transition penalty. No physical bend
   ID is globally forbidden; the same bend can pass in another prefix.
8. Extract the **authoritative unfolded external outline** (not panel
   bounding boxes, individual panels or inner holes). Collapse small
   tessellated/collinear edges. Four external edges are named LEFT, RIGHT,
   TOP and BOTTOM; hexagons get SIDE_1 through SIDE_6. Other simple
   polygons get as many seeds as exterior sides, up to 24. If geometry
   cannot be classified, report OUTER_CONTOUR_UNAVAILABLE.
9. Run one bounded Beam Search **for every exterior side**, forcing a
   nearby, approximately parallel first bend and its operator-facing
   0/180 loading orientation. The 2.5 s recovery time budget is shared
   among sides, not multiplied by their number. A side lacking a parallel
   bend receives an explicitly labelled approximate start; manual pins
   always remain authoritative. Each side reports whether it found a
   full sequence or ran out of time.
10. Append distinct forced-side candidates to the FAST list and
    screen the expanded set again with Stage B. Reuse the same per-dialog
    transition cache to avoid recomputing the original candidates.
11. Results rerank but **do not delete** existing FAST options. The UI
    maps displayed rows to original candidate indices before applying.
    Side names appear with every newly generated candidate. All physical
    clearance statuses remain `UNVERIFIED`.

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
- First 2D `budget_seconds=3.0` and second expanded-list 2D
  `budget_seconds=3.0` are separate *cooperative* budgets. Stage B
  tests the deadline between samples but individual calls may overrun.
  Existing transitions are cached across both passes.
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
   sequences are found. A rectangular outer contour should show all
   four side findings in the debug log, and a simple hexagon all six.
5. Confirm that side candidates have distinct leading bends where
   geometry permits, and operator-facing 0/180 starts.
6. Try different 0/180 choices. Confirm the *actual* selected candidate
   (not the displayed row index) is applied.
7. Confirm operation table, simulation and document windows remain intact.
8. Inspect `SMBS_debug_log.json` for `FAST_SEARCH_RESULT`,
   `FAST_SHORTLIST_2D_SCREEN_INITIAL`, `FAST_FORCED_ALL_SIDES_RESULT`,
   `FAST_SHORTLIST_2D_SCREEN`, `FAST_RESULT_DIALOG_FINISHED`, and
   `FAST_CANDIDATE_APPLIED`.
9. Record `side_findings` including each `status`, per-side
   `first_orientation_by_operation`, `returned_candidates`,
   `new_candidates`, `elapsed_seconds`, `actual_probes`, `cache_hits`,
   each `first_issue`, and any `BUDGET_EXCEEDED`. Compare against
   observed native 3D/tooling contacts.

Pure Python regression:
`python -m unittest tests/test_fast_shortlist_screening.py tests/test_fast_multiside_feedback.py tests/test_fast_search_gui_contract.py`

## Next steps, *not* implemented yet

- Validate PREBEND and FINISH partial-angle paths with a stage-aware
  sweep; Stage B's current sweep assumes the full bend final angle.
- Compare actual 2D gauge contacts against the CAD contact model, accounting
  for virtual tooling and station alternatives.
- Side-based reruns currently occur once per outer edge. If these
  still all fail, consider additional feedback iterations with a strict
  global time/branch limit and a measurable progress criterion.
- Validate the full machine pose and gauge contact for each starting
  exterior edge; this release uses authoritative unfolded geometry as
  a search seed, not a certified global loading placement.
- Run independent multi-section mesh and full physical checks on selected
  candidates; do not turn sampled-clear into a safety certificate.
