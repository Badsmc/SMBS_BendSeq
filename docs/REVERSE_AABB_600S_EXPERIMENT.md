# Experiment: Reverse AABB unbending search (600 s)

This branch is isolated from `main`, `smbs`, and the earlier fast-feasible
search. Do not merge until the FreeCAD regression fixture has been tested.

## Current implementation

1. Load SheetMetal-derived panel graph and original FreeCAD panel-face outlines.
2. Use the target signed bend angles as the fully formed starting state.
3. DFS over inverse bend-removal moves (reverse unbending).
4. Build 3D AABBs for each rigid panel with the existing FoldState transformations
   at intermediate unbend fractions (0.75, 0.50, 0.25, 0).
5. Rank moves by nonadjacent panel AABB overlap; cache states and transitions.
6. Return candidate *forward* orders by reversing the unbend path.
7. Verify candidates independently using the existing native D6 forward replay
   against the selected punch/die. Virtual-length mode remains explicitly
   non-physical; the forward replay does not certify loading, backgauges,
   clamps, frame, transfers or continuous trajectories.
8. If reverse geometry is unavailable or no full candidate is found, fall back
   to the existing 2D search.

## Important limits

**The reverse search currently uses panel-panel AABBs as a ranking heuristic.**
It does **not** yet implement swept machine-space BBox collision against
punch/die, machine frame, or backgauge. AABB *overlap* is never treated as
proof of collision; AABB *separation* is meaningful only for the specific
sampled pair and instant, not as continuous clearance.

Every reverse order has evidence
`REVERSE_AABB_RANKED_UNVERIFIED` until the independent native D6
replay succeeds. Its default orientation is a candidate, not a validated
mounting direction. Search does not alter the operator's FoldState.

## Budget policy

Experiment GUI: 600 seconds, best effort (a running OCC operation may exceed
the wall clock deadline). Reverse candidate generation: at most 90 seconds.
When reverse candidates exist, skip the costly planar DFS and spend remaining
time on the forward D6 validator. Otherwise use up to 150 seconds for existing
planar DFS and reserve remaining time for 3D validation.

## Check before merging

- Run `python -m unittest tests.test_reverse_aabb_search` outside FreeCAD.
- In FreeCAD 1.1, test the 10-bend fixture with selected FCStd tooling and
  virtual tool length enabled; collect the full SMBS debug JSON.
- Confirm that `reverse_search.geometry_available` is true and
  `reverse_search.candidates` is nonempty in the finish event.
- Confirm *full* forward physical replay, orientation, and geometry
  before treating any candidate as sampled collision-clear.
- Compare elapsed and exact outcome against the prior 60-second 2D approach.

## Next development milestone

Add actual moving punch/die tool AABB and OBB broadphase in machine coordinates,
followed by swept-path conservative screening, pose/turn alternatives, and
selective exact OCC narrowphase. Only safe-proven exclusions may prune;
ambiguous contacts must remain UNKNOWN or defer to D6.

## Results from 2026-11-16 Windows debug log

Two 600-second test invocations took 104.21 and 103.35 seconds. AABB DFS
explored 1024 states / 14800 transitions in about 14 seconds and produced
three full permutations with overlap scores of zero. Each 3D candidate
stopped at its first bend (B09, B10, or B01): BENDING intervals exceeded
the old 6-second phase limit; one bounded retry with a ~9-second cap
also exhausted. Recorded sampled punch overlaps were <= 0.000098 mm³;
these are **not proof of a blocker**. No sequence was physically validated.

Follow-up changes on this experimental branch:
- Raise D6 per-phase allowance to 24 seconds and timeout retry up to 40s
  while respecting the overall deadline and preserving UNVERIFIED.
- Diversify reverse search by first forward bend and root-unbend path,
  rather than testing multiple almost identical first operations.
- Exclude unnecessary fallback operator seed when reverse candidates exist.
- Label simulator preview as AABB/DFS UNVERIFIED (never 2D-cleared).
- Continue to treat zero AABB-overlap score as a weak ranking result, not
  evidence of actual 3D machine clearance.

The new tuning requires physical FreeCAD tests; elapsed performance is
not established by static code changes.

## Follow-up: B08 adaptive validation and pose diversity

After the third Windows log (`SMBS_debug_log(20261116-074414).json`):
- Reverse DFS evaluated 4,620 unique states and 58,538 transitions,
  producing three full 10-bend **candidates**, not validated production orders.
- Native D6 advanced through B09 and B10 but B08 remained UNVERIFIED during
  BENDING. The third search invocation ended after ~410 seconds out of 600.
- The exact per-pose native collision oracle already uses FreeCAD BoundBox
  conservative gap as an early CLEAR reject before expensive OCC common/
  distance queries (`native_collision_3d.py`). No parallel, competing
  punch/die broadphase has been added.

This patch:
1. Shares a bounded-invocation **fully sampled-clear bend-step cache** across
   candidates. Keys include underlying graph/panel identities, actual tool
   sources and object identities, assembly offsets, every adaptive setting,
   completed fold set, active bend and workpiece orientation. Reuses ONLY
   accepted native steps with sampled-clear retract and punch/die-tool pair;
   never caches timeout, conditional, or collision as success.
2. Adds explicit 180-degree orientation alternatives for B08 and candidate
   first operations. Every variant still goes through exact native D6.
3. Raises experimental phase limits to 40 seconds, 72 states, recursion
   depth 7, with at most one bounded 60-second recheck, remaining subject to
   the overall 600-second soft deadline.
4. Adds `tests/test_fast_reverse_orientation.py` for noncertifying pose
   expansion. Native FreeCAD/OCCT benchmark has NOT been run in this chat.

Remaining work: measured production-quality swept BBox/OBB against the
machine and an independent continuous collision/transfer proof. This
experiment reports sampled-tool clearance at best, not machine approval.

## A* + reusable mesh / BVH implementation (2026-11-16)

- `reverse_mesh_screen.py`: triangulates each available FreeCAD panel
  `Part.Face` once via `tessellate()`, with a per-panel triangle cap and
  explicit fallback to AABB when triangulation is unavailable.
- Retains local triangles; for sampled reverse-unbend intermediate poses,
  uses the existing deterministic `FoldState` rigid transforms to construct
  thin triangular prisms without OCC Booleans or shape transformations.
- Each transformed pose has a bounded 48-entry LRU cache of triangle prisms
  and binary AABB volume hierarchies (BVH).
- Narrowphase uses convex triangular-prism separating-axis tests after BVH
  overlap checks. Approximate interference **only affects ranking**;
  it never rejects a transition or produces a physical CLEAR certificate.
- Replaced the first-found reverse DFS with bounded A* queue exploration,
  `g = number_of_unbends + nonnegative_interference_penalty` and
  `h = remaining_bends`. Its heuristic is an admissible count lower bound
  for this ranking cost, NOT a claim of machine-optimal manufacturability.
- The native forward D6 validator still checks original FCStd punch/die
  geometry and all sampled phases. The existing native `BoundBox` broadphase
  remains the tool-pair filter; machine-space **mesh** collision screening,
  continuous swept paths, clamp/frame/backgauge verification, and STEP import
  are **not yet implemented** in this experiment.
- Candidate slots now reserve capacity for explicit 180-degree flipped
  loading alternatives instead of filling all slots with normal orientations.
- CI workflow `.github/workflows/reverse-mesh-experiment.yml`
  runs syntax compilation and pure-Python search/mesh tests on this branch.
  Real FreeCAD tests and speedup benchmarks are still required.

The 600-second overall experiment budget and 90-second reverse candidate
generation soft limit remain unchanged. The next debug log will include
`reverse_search.mesh_panels`, `mesh_triangles`, `mesh_overlap_tests`,
and `search_mode` for confirming that actual FreeCAD triangulation ran.

## Machine-space native punch/die AABB candidate ranking

Following the 08:37 log, self-collision panel BVH narrowphase was invoked
zero times because all candidate panel AABBs were separated. The native D6
could verify some first three bends but repeatedly left B07's BENDING
interval UNVERIFIED. This is missing evidence, not a proven collision.

The updated 600-second experiment now also includes
`reverse_machine_screen.py`:

- A* generates up to ten distinct first-bend candidates using the
  existing lightweight 3D panel representation. It reserves a bounded
  tail of the **90-second reverse search budget** for tool ranking.
- The source of the punch/die is the *selected verified FCStd profile*
  through `physical_validation_25d._source_path`, then the **same**
  `native_tool_loader.load_native_punch/load_native_die` used by D6.
  Virtual tool lengths and explicit section offsets retain their
  existing semantics. Loaded tool envelopes are reused by length.
- For each complete candidate, `WorkpiecePlacement3D.compute_placement`
  aligns the active fold axis in actual machine coordinates. Both
  `reverse_axis=False` and `True` are sampled at 0%, 50%, and 100%
  active fold. BBox overlap and short-range clearance of each panel
  relative to both tools give a **noncertifying** cost for sorting.
- Machine AABB is a conservative *broadphase*, and the sampled punch
  heights (tip at 6.5 and 1.5 mm) are **representative only**. This is
  NOT a continuous press stroke, NOT proof of loading/transfer and
  NOT a machine-tool mesh intersection implementation. A candidate
  with score zero remains UNVERIFIED and must pass D6.
- Failure to obtain an actual selected tool envelope or compute a pose
  is recorded explicitly and leaves the candidate eligible for D6.
  Nothing substitutes a fictitious tool.
- D6 is still required to assess the actual selected punch/die geometry
  during forward BENDING and other phases. Sampling remains
  noncontinuous and excludes complete frame/backgauge/clamp proof.

New debug report keys are
`reverse_search.machine_screen_status`,
`machine_screen_pairs`, `machine_screen_overlaps`, and
`machine_screen_candidates`. A meaningful experimental run should
show `machine_screen_status=NATIVE_TOOL_ENVELOPES_LOADED` and
`machine_screen_pairs>0`; otherwise the new tool stage was inactive.

To validate properly, run the updated install in FreeCAD with the
ten-bend fixture and selected punch/die. Inspect the full JSON,
compare the actual 600s search wall time, number of *completed D6*
bends, cache HIT counters and machine-screen diagnostics.
Do not call the search manufacturing-ready unless a complete
forward sequence with physically required setup/transfer constraints
has been verified.

## Adaptive pose retry following native D6 evidence

The earlier candidate expansion hardcoded B08 and the first two bends. In
the 08:37 Windows run it consumed limited 600s capacity on unrelated
early-fold flips while B07 was the actual blocking/unresolved operation.
The experiment now reserves D6 slots for *on-demand* pose alternatives:
after a full candidate fails with UNVERIFIED, it identifies the first
non-clear step by native D6 output, toggles ONLY that bend's 180-degree
orientation, and schedules an independent replay. The modified variant
is never auto-accepted; proven sampled clearance still requires every
step plus tool-tool/retract evidence. The 600s budget and deduplication
limit still apply.

The machine envelope phase is only a cheap preliminary ranking. A
zero-overlap result says nothing about uninspected swept trajectories or
transfer/loading. Production-grade qualification also requires verified
backgauge, ram/frame and clamp clearance and real tool length.

## Test log 2026-11-16 09:34:31 and evidence-driven order retry

The supplied Windows log reports 565.1058 seconds overall (600s budget),
17.5835 seconds reverse A*, 11 panels / 38 triangles, 5,121 visited
reverse states and 64,990 explored reverse transitions. The selected-tool
machine screen succeeded (`NATIVE_TOOL_ENVELOPES_LOADED`), with 2,970
machine-space AABB comparisons, 521 box intersections and 3 evaluated
full orders. The native strict per-step reuse was effective: 16
`MANUAL_NATIVE_VERIFIED_STEP_CACHE_HIT` events and five stores.

All nine native attempts remained UNVERIFIED, generally at B07 BENDING
(`MAX_TIME_BUDGET_EXCEEDED`), not because a forbidden collision was
proven. Sample punch overlap peaked at 0.000098 mm³, die overlap at 0.
Per-phase B07 solids were reconstructed ~15-22 times in the slowest
attempts. These facts locate the bottleneck in native OCC validation
rather than coarse candidate generation. Panel mesh narrow-phase remained
unused (`mesh_overlap_tests=0`) because candidate panel AABBs did not
overlap; this alone is not a code fault.

A new D6-feedback retry now proposes delaying the unresolved bend by two
positions within the forward sequence before rechecking the same prefix
with a 180-degree flipped workpiece. Both remain independently
UNVERIFIED until an entire D6 forward replay succeeds. A regression test
checks the alternative order. This is experimental code, untested here in
a FreeCAD process, and does not establish a manufacturing-ready route.

## Desktop benchmark 2026-11-16 10:00:28

One FAST_FEASIBLE request completed with status
`NATIVE_CANDIDATES_REJECTED_OR_UNVERIFIED` in 575.4006 s, under the
600-second soft budget. The reverse A* took 17.5902 s, identified three
full machine-ranked orders (11 panel meshes, 38 triangles, 2,970 tool
AABB comparisons, 521 intersections). Eight D6 candidates were attempted;
26 strict verified-step cache hits and 3 stores occurred. The deepest
independently verified prefix remained three bends: B10, B09, B08.
Order feedback correctly tried B07 later; instead B06 or B05 became the
first unresolved BENDING step. This is not proof of a physical obstruction.

The first attempt consumed 260.2232 s through repeated verification
of the same order despite `BENDING=INTERVAL_UNRESOLVED`, mostly within
native OCC geometry and collision calculations. The next two reorders
cost 112.1355 s and 75.4199 s, respectively. The fast search now limits
initial D6 phase allowance to at most 18 s (previously 40 s) and
does not immediately invoke the extended same-order retry while other
candidate permutations remain. A deep retry requires the prior run to
reach at least the penultimate bend and sufficient remaining time.

CRITICAL PHYSICAL SCOPE: The selected source tools are only 100 mm
long, while B07 is approximately 498.18 mm long. The experiment had
`match_active_bend_length=True`, creating virtual tools as long as
the active bend. Even completing the virtual search would NOT certify
physical execution with the selected 100 mm tools. Use verified actual
machine tool assemblies or sufficiently long real FCStd tools before
manufacturing readiness claims.

No native Windows FreeCAD test of the revised budgets has yet been run.

## User scope clarification: tool SECTION is mandatory, tool LENGTH deferred

The primary objective is now to rank complete bend orders on parts of
varying complexity with actual *selected punch/die cross-sectional*
geometry. The physical assembled tool length is intentionally NOT a
constraint during candidate search: the experimental UI exposes
`Поиск порядка — профиль оснастки (120 с)` and calls reverse A*
with `virtual_tool_length=True`. This loads native FCStd punch and die
using the same profile source as D6, with effective length equal to each
active bend, while leaving operator tooling settings unchanged.

The 120-second candidate stage scores panel intersections via cached
AABB / BVH and selected punch/die envelope. It additionally samples
the selected tool's 2D contour (including convex/concave silhouette)
against projected panel YZ envelopes. Thus a gooseneck recess need not
incur the same scoring penalty as a solid rectangular tool.
This is still APPROXIMATE candidate ranking, not exact 3D collision
proof. A candidate is marked
`TOOL_PROFILE_VIRTUAL_LENGTH_AABB_UNVERIFIED` in the UI/log.
No silent fallback to generic rectangular tooling is permitted in this
specific mode; when either tool section is unavailable the UI leaves
the existing sequence unchanged and reports the failed source.

Existing `Поиск + 3D-проверка` and manual D6 remain independent
and are deliberately not replaced. Their native physical tool-length
mode remains operator-controlled. No statement of global optimality or
machine feasibility is justified solely by the 120-second ranking.

## One-click integrated workflow (2026-11-16)

Operator sequence search is now a **single** visible primary FreeCAD
action: `Найти последовательность (до 600 с)`. The short-lived
`Поиск порядка — профиль оснастки` action has been removed. The
legacy 2D-only entry remains hidden for developer diagnostics only.

On clicking the primary action, the program automatically:

1. Checks the selected SheetMetal geometry and punch/die profiles.
2. Creates an isolated search-time tooling setup: actual selected tool
   cross sections but virtual tool length equal to the active bend.
   Physical tool section offsets are ignored; user UI settings are not
   mutated.
3. Generates reverse A*/Mesh/BVH order candidates, ranks them using the
   selected tool CAD envelope and sampled 2D contour, and selects
   180-degree placements.
4. Runs native D6 on ranked candidate orders with **the identical
   virtual-length tooling mode**. D6 feedback can queue alternative
   bend order/placement trials within the shared 600s soft budget.
5. Adopts a winning complete sampled candidate where qualified; otherwise
   offers an explicitly UNVERIFIED full-order preview and logs reasons.

This pipeline does NOT give continuous collision certification or prove
gauge/frame/clamp/transfer feasibility. Machine-length checks remain
separate for eventual production use. The integrated pipeline is
implemented in GitHub but requires a new Windows FreeCAD run before
performance or correctness claims.

## Visible operator sequence result and simulation handoff

The 600-second single-button search now explicitly displays the ranked
order rather than only a generic `NATIVE_REJECTED_OR_UNVERIFIED` status:

- During D6, the Search page displays the current complete candidate.
- At finish, `SMBS_SearchResultSequence` lists every bend in order,
  its 0/180-degree placement, D6 checked-step count, number of attempts,
  and the virtual-length/physical-validation limitations.
- The best displayable unresolved order is chosen from actual D6
  attempts by the highest checked-step count; this can be an
  evidence-driven reorder, not the first reverse A* candidate. Explicit
  physical rejections are excluded. A complete A* order remains a
  fallback only when it was not physically rejected.
- If a complete order is available, the search applies it to the
  session, configures the existing PressCycle controller, switches
  the native Tasks page to **Симуляция**, selects the 2.5D viewer,
  displays the order above Play/slider, and retains it as
  `UNVERIFIED_D6_INCOMPLETE` in saved choices when necessary.
- If no complete order is found, the GUI must say so and keep the
  operator's existing sequence; a partial prefix is NOT masqueraded
  as a ten-bend solution.
- JSON results expose `preview_sequence`,
  `preview_orientations`, `preview_checked_bends`,
  `preview_origin`, plus `FAST_FEASIBLE_PREVIEW_ADOPTED`
  events. No D6 safety or global optimality claim is made.

These changes require a real FreeCAD GUI run before confirming playback
works in the target Windows installation.

## Windows run 2026-11-16 10:20-10:25, user-uploaded log

The runtime was installed from a copy with `git_branch=UNKNOWN`,
`git_commit=UNKNOWN` and GUI event
`GEOMETRY_ONLY_SEARCH_REQUESTED`. This is an **older UI generation**
than the one-button GUI commits. Do not interpret its missing simulation
result card as evidence that the latest GUI was tested.

- Source FreeCAD unfold: 11 panels, 10 bends.
- Reverse search generated 3 complete ten-bend orders with selected
  punch/die section and virtual length. The lowest approximate score
  order was
  `B10 B09 B08 B07 B06 B05 B04 B03 B02 B01`
  (score ~530.94; UNVERIFIED).
- Reverse A* time ~15.3 s; one-click native search ~193.44 s.
- 8 native D6 attempts: **all eight stopped at the very first bend**.
  D6 reported `BENDING=INTERVAL_UNRESOLVED` and a per-phase timeout
  at ~18-19s. Peak reported punch overlap was tiny (~0.000007 mm3);
  this is *not* a certified no-collision result.
- Both `GEOMETRY_ONLY_PREVIEW_ADOPTED` and
  `FAST_FEASIBLE_PREVIEW_ADOPTED` were emitted. However the older UI
  changed the view to flat pattern and did not provide an obvious
  persistent numbered result in Simulation.

Corrections committed after this log:
1. On one-button search completion, show a numbered 10-bend order,
   orientations, and native-D6 status persistently on the search page.
2. Switch to the Simulation native Tasks page and install that order
   and orientation for playback; label all unqualified motion UNVERIFIED.
3. Keep preview orders in saved-sequence choices, and prefer the
   highest reached unresolved native attempt over the initial A* order.
   An actual native rejection cannot displace an unverified order.
4. Restore 40-second initial D6 phase allowance after the 18-second
   experiment repeatedly stopped *before* the second bend.
5. Distinguish `D6 reached step N` from `N bends physically verified`.

These changes are not yet validated in the running Windows FreeCAD
instance. The search algorithm does not guarantee a globally optimal
or manufacturing-qualified sequence.

## 2026-11-16 11:16:38: invalid operator-facing sequence report

User rejected the returned sequence as unworkable. The attached log is
`SMBS_debug_log(20261116-111638).json`, loaded at 11:07 UTC.

FACTS from this log:

- Reverse A* considered 5,121 visited subset states, 64,990 transitions,
  scored 3 full sequences. `machine_screen_pairs=2970` and
  `mesh_overlap_tests=0`. AABB / tool-profile scores are
  **noncertifying** and were misleading as an "optimal" result.
- Fast search took 441.8425 s and performed 8 independent D6 attempts.
  Every D6 result was UNVERIFIED. No complete all-bend native clearance
  existed; maximum reached step was 3/10.
- D6 converged B10 and B09 for the top-ranked sequence, then failed to
  converge B08 BENDING (no PROVEN_BLOCKING collision). Other attempts
  moved the unresolved point to B07 or B06, or failed on B10/B08 early.
- Nevertheless the old UI emitted `FAST_FEASIBLE_PREVIEW_ADOPTED`
  with `B10 B09 B08 B07 B06 B05 B04 B03 B02 B01`. User verified this
  sequence was unworkable.

Corrections:

1. **Never** automatically replace the current bend order / play / save
   an unverified A*-only sequence. It stays a visibly labeled candidate
   in the search panel. The GUI emits
   `FAST_FEASIBLE_CANDIDATES_NOT_ADOPTED`.
2. Search generates more forward-prefix-diverse A* full-order
   candidates, rather than one per first bend.
3. An existing operator-selected complete order is also run through
   D6 as a distinct candidate even when A* returned others.
4. Add regression tests for the operator seed and for the explicit
   separation between incomplete native evidence and qualified results.

The above is a correctness/UX safeguard and a broader candidate search,
NOT a newly proved feasible 10-bend order. The real solution still
requires per-bend geometry-valid successor transitions and a runtime
3D verification strategy that does not repeatedly hit OCC BENDING
timeouts. Any false implication of optimality must be rejected.

## Architectural correction: sampled 2D search restored as primary (2026-11-16)

The user reported that the original 2D engine already generated nearly
usable bend sequences. Inspection confirmed a regression: the experimental
fast search executed `search_reverse_aabb` first and set
`SKIPPED_REVERSE_AABB_CANDIDATES_AVAILABLE` when any A* permutations
were returned, so the stronger legacy Stage A/B 2D kinematic checks
were never run on those orders.

The one-button search now executes **`search_bend_sequences_2d`
first**, with the selected real punch and die cross-sectional geometry
and virtual lengths at each bend, with a bounded search budget of up
to 210 seconds or 35% of total budget. It uses cheap Stage A screening,
sampled Stage B collision checks, then the original full-density
candidate replay before returning complete 2D orders. Existing
memoization, grouped search, source-state restoration, and
operator-selected scoring weights are retained.

Only if no complete 2D candidate survives does the system use A* as a
**fallback** source of unverified orders. Complete 2D candidates are
passed to independent native D6 physical validation with the same
virtual-length tooling. A D6 timeout is UNKNOWN, never proof of
collision or clearance. An unverified full order is reported but cannot
be auto-applied to playback as a proven feasible sequence.

Performance improvements should now target:
1. Stage A/B transition timing and caching for state + bend + orientation;
2. cheap polygon/section rejection and correct tool profile projection;
3. limiting expensive OCC to a small shortlist of full 2D candidates;
4. measured replay accuracy on this 10-bend test, rather than faster
   A* enumeration alone.

No performance improvement or full 10-bend physical result is yet
confirmed in Windows FreeCAD. Regression test
`test_sampled_2d_candidate_is_primary_and_skips_reverse_a_star`
requires that valid 2D candidates bypass fallback A*.

## Five-angle native OCC replacing adaptive D6 inside automatic search

The user explicitly requested that the expensive adaptive native D6
candidate replay be replaced by the already implemented *five-angle*
OCC screen. The one-button sequence pipeline now operates:

1. The existing Stage A/B 2D DFS is primary; sampled 2D full candidates
   are prioritized. If none are found, reverse A* generates unverified
   alternatives.
2. A shortlist is independently replayed with the actual selected
   FCStd punch/die cross sections, and axial tool length is virtualized
   to the active bend.
3. Each bend uses `AdaptiveSamplingConfig(coarse_occ_screen=True)`.
   In BENDING the engine checks precisely the fixed fractions
   **0, 0.25, 0.50, 0.75, 1.00** of the target bend angle. APPROACH and
   RETRACT use 0, 0.5, 1.0; CONTACT/BOTTOM use the designated endpoint.
   No recursive interval subdivision is performed for this screen.
4. A complete candidate is accepted as
   `FIVE_POINT_NATIVE_OCC_CLEARANCE_UNVERIFIED` only when the existing
   native validator returns **one step for every bend**, in the exact
   proposed order and orientation, with the verified status
   `COARSE_3D_NO_PROVEN_COLLISION` on every bend and no conditional
   end-zone warning, and tool sources and length mode match.
5. Collision, unknown pose, error, missing source geometry, timeout,
   incomplete report or partial result is **not** promoted to a pass.
   The previous same-order adaptive retry has been removed from the
   automatic workflow. The slower independent full D6 remains available
   as an explicit manual diagnostic if needed.

CRITICAL INTERPRETATION: five discrete bend angles do not prove
clearance between those angles, especially for a long flange sweeping
past a gooseneck punch. Five-angle `no detected collision` means only
that the sampled positions had no proven blocking collision. The
interface marks this as a preliminary, unverified manufacturing plan
and never sets `continuous_motion_certified` or
`machine_feasible_certified`.

The change is committed and covered by the five-angle classification
and integration regression tests, but not yet measured with the user's
FreeCAD/Windows OCC runtime. Therefore a <1–3 minute total runtime is
a target, not a benchmarked result.

## Automatic endpoint-only OCC experiment, 1000-second budget (2026-11-16)

Requested change: replace five bend-angle samples
`(0.0, 0.25, 0.5, 0.75, 1.0)` in the automatic search with
**`(0.0, 1.0)`** (start and target angle), and increase the
one-click total search time budget from **600 s to 1000 s**.

Implementation:
- `AdaptiveSamplingConfig.coarse_occ_bending_fractions` is now
  explicit; default remains the existing five-angle sampling for
  independent manual diagnostics.
- Automatic `fast_feasible_search` sets
  `coarse_occ_screen=True` with
  `coarse_occ_bending_fractions=(0.0, 1.0)`.
- APPROACH and RETRACT retain their existing
  `(0.0, 0.5, 1.0)` ram-position samples; CONTACT and BOTTOM
  retain their existing single sample.
- The native result's `validation_mode` is
  `COARSE_OCC_2_BEND_ENDPOINTS`. Only a complete report matching
  the selected bend order and orientations, with
  `COARSE_3D_NO_PROVEN_COLLISION` for each step, qualifies for
  **`TWO_ENDPOINT_NATIVE_OCC_CLEARANCE_UNVERIFIED`**.
- The FreeCAD search button, progress text, serialized request log,
  and timeout message reflect the 1000-second and two-endpoint mode.
  No other 600-second benchmark mode is modified.
- Existing discrete-only caution still applies. A collision may
  occur at an untested bend angle or ram position. The returned
  sequence remains a **preliminary preview, not physically certified**.

Source benchmark (new user log
`SMBS_debug_log(20261116-115208).json`): previous 600 s
run consumed 603.15 s total, including 229.80 s 2D search.
Five native OCC order attempts were made. Four produced explicit
`COLLISION` on B03 (two attempts reached B03 as their eighth
operation), and the fifth was stopped by budget. This is a
baseline, NOT evidence that endpoint-only sampling resolves B03.
Re-run under the new source identity and compare evaluated OCC
states, time and failures before claiming improvement.

## Operator punch CONDITIONAL: independent LEFT and RIGHT end zones

The one-button automatic Stage A/B -> OCC 0/100 search now **respects the
existing GUI selection** `allow_end_zone_collision` and its configured
`end_zone_length_mm`. Previously both stages explicitly overrode
these to `False` / `0.0`, so conditional clearance was ignored.

The existing native CAD classifier already treats the user-entered
length **per axial end**, in machine coordinates:

- LEFT: `[punch_xmin, punch_xmin + L]`
- RIGHT: `[punch_xmax - L, punch_xmax]`
- CENTRAL (strictly blocking): `(punch_xmin + L, punch_xmax - L)`

For a virtual 500mm punch and L=10mm, these are
[-250,-240] and [240,250], NOT 5mm at each end. An intersection
qualifies as `END_ZONE_ONLY` only if the native full 3D OCC
classification locates the entire significant punch collision volume
within one or both zones. Significant central and mixed collisions,
die collisions, invalid geometry, and ambiguous classification are
NOT granted an exception. A nonpositive zone width is invalid when
the permission is enabled; two zones covering the entire punch are
unclassified and cannot be approved.

The native 0/100 result can now be either:
- `TWO_ENDPOINT_NATIVE_OCC_CLEARANCE_UNVERIFIED`: no proven collision
  in sampled states;
- `TWO_ENDPOINT_OCC_CONDITIONAL_PUNCH_END_ZONES_UNVERIFIED`: only
  explicitly permitted punch END_ZONE_ONLY collision occurred in sampled
  states (all bends checked, orientations matched, no unknown or
  central/matrix hit).

Both are PRELIMINARY and NOT machine-feasibility certifications.
A permitted end-zone overlap is NOT a physical relief cut in the actual
punch model. Actual punched tool relief, continuous motion, uninspected
angles and real installed tool length remain separately unverified.
The conditional status is visible in Simulation and logged for each
bend. Regression tests explicitly cover both end zones, a central hit,
missing permission, missing length and wrong orientation.

### Conditional and the 2D-only ambiguity (follow-up)

The existing `STAGE_B_SECTION_REQUIRES_3D` was previously converted
unconditionally into `2D_INTERFERENCE`, and that branch was pruned.
When punch conditional relief is enabled, 2D cross-sectional geometry
cannot localize this collision to the axial LEFT/RIGHT ends, so it
cannot prove an exclusion. The main DFS now treats a **PUNCH-only**
`STAGE_B_SECTION_REQUIRES_3D` finding as `UNVERIFIED_2D` *only when*
the operator explicitly enabled conditional ends. Such a candidate
must still pass the native OCC 0/100 classifier to be reported as
`CONDITIONAL_END_ZONES`; it is NOT promoted to `SAMPLED_2D_CLEAR`.
DIE, BACKGAUGE, missing finding evidence and other interference
remain rejected by the existing 2D classifier.
