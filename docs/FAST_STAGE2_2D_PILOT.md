# FAST shortlist: bounded advisory 2D screening (experimental)

Branch: `fix/fast-axis-soft-penalty-20261120`

## Intent

Keep the existing ~1 second FAST Beam Search independent of native geometry.
After the FAST result dialog opens, run a **separate** advisory Stage A/B
screening over the returned (up to five) candidate sequences, with a **3 s
cooperative budget**. Prefer sampled plans with fewer 2D tool-interference
indications, without claiming real-machine or continuous-3D feasibility.

Sources: `fast_bfm_beam_search.py` (generator),
`fast_shortlist_screening.py` (new shortlist post-processor),
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
7. Results rerank but **do not delete** existing FAST options. UI maps
   each displayed row back to its original candidate index before applying
   it. The FAST result and chosen orientation retain `UNVERIFIED` status.

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
- 2D `budget_seconds=3.0` is a *cooperative* budget. Stage B tests the
  deadline between samples but individual geometry calls may overrun.
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
4. Confirm results appear after the original ~1 s search; then a second
   `2D:` status appears for each candidate.
5. Try candidates with different 0/180 choices. Confirm an actual chosen
   candidate (not its new display-list row index) is applied.
6. Confirm operation table, simulation and document windows remain intact.
7. Inspect `SMBS_debug_log.json` for `FAST_SEARCH_RESULT`,
   `FAST_SHORTLIST_2D_SCREEN`, `FAST_RESULT_DIALOG_FINISHED`, and
   `FAST_CANDIDATE_APPLIED`.
8. Record `elapsed_seconds`, `actual_probes`, `cache_hits`, each
   `first_issue` and any `BUDGET_EXCEEDED`. Compare against actual 3D
   contact and collision observations.

Pure Python regression:
`python -m unittest tests/test_fast_shortlist_screening.py tests/test_fast_search_gui_contract.py`

## Next steps, *not* implemented yet

- Validate PREBEND and FINISH partial-angle paths with a stage-aware
  sweep; Stage B's current sweep assumes the full bend final angle.
- Compare actual 2D gauge contacts against the CAD contact model, accounting
  for virtual tooling and station alternatives.
- If all returned candidates have 2D evidence of interference, feed
  transition-level risk back into FAST and request **new diverse candidates**,
  using cache and a separate timeout; the pilot currently only reranks
  FAST's existing shortlist.
- Run independent multi-section mesh and full physical checks on selected
  candidates; do not turn sampled-clear into a safety certificate.
