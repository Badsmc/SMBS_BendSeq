## 2026-11-11 — after external rear-contact solver (100722 CAD trace)

Provided FreeCAD files: `smbs_2d_search_diagnostics(20261111-080809).json` and `smbs_backgauge_cad_20261111-100722_23fe6ed3.jsonl`.

**Observed, not inferred:** 33 CAD automatic setup requests, 33 `EXTERNAL_REAR_CONTACT_TRIAL`, and 33 `EXTERNAL_REAR_CONTACT_SOLVED`; no trial was rejected by the real OCC stop-vs-sheet geometry at the sampled setup. Selection uses `Pad.Face6` and external support panels such as P07/P02/P11 instead of mandatory bend-kinematic `fixed_panel`. This eliminates the earlier blanket root `CAD_3D_INTERFERENCE`.

The 2D DFS report now records 28 sampled-clear-or-touch transitions, four 2D tool interferences, and one `UNVERIFIED_2D` timeout; 33 evaluations, 21 visited nodes, 30.05s consumed, `MAX_SEARCH_TIME_REACHED`, `max_depth_reached=6`, and no complete sequence. Deepest path `B10 → B09 → B08 → B07 → B06 → B05` has five sampled-clear steps; B05 ended with `2d_probe_time_budget_reached`, so the sixth bend is **not physically proven**. 2D interferences include P05 vs punch during B10 `BENDING`, and P05 (plus B06 zone in one state) vs die during B09 `APPROACH`. Do not weaken these collisions.

**Performance adjustment:** For 10+ bends **with real CAD backgauge enabled**, GUI preliminary search budget rises from **30s to 90s**, and fair initial first-bend probe windows shrink from 1.8s to 0.9s to leave more time for uninterrupted deeper DFS. Other searches remain 30s. The UI shows the configured budget; diagnostics contain `PLANAR_2D_SEARCH_COMPLETED.budget_seconds` in the normal FreeCAD file log. Sampling density, OCC volume thresholds, and rejection policy are unchanged. No claim a 7/8-bend prefix will necessarily be found.

**Unverified machine-axis travel:** source trace includes calculated stop-contact Z_m of approx `-383.94 mm` and `+248.75 mm` in some paths. These are actual geometrically matching model poses, **not** evidence the physical gauge has that R-axis motion. Neither vertical travel limits nor continuous stop/piece/punch/die transfer are currently qualified; such setups must not be presented as certified machine programs.

Re-run on the same ten-bend fixture with one `Pad.Face6` CAD gauge at X_m=0 and no configured gap. Provide fresh search JSON plus CAD JSONL. If still resource-limited, investigate Stage B OCC cost, incremental caching, and machine-axis constraints rather than suppressing physical collisions.


## 2026-11-11 — real Face6 and accessible external rear edge (094252 diagnostics)

The user supplied two matching files: `smbs_2d_search_diagnostics(20261111-074316).json` and `smbs_backgauge_cad_20261111-094252_2080ba17.jsonl`. The selected `Pad.Face6` survives as a final `Body.Face6`, has normal `(0,-1,0)`, and is now rotated **180°** around Z to machine-facing `+Y_m`. The placed CAD body extends **200 mm behind its actual contact face**; the previous 25 mm forward intrusion is **zero** (`front_protrusion_mm=0`). These are verified by the supplied FreeCAD log; not inferred from a bounding-box-only test.

With the old automatic policy, all ten root bends failed at `CONTACT_SETUP` because `fixed_panel` was incorrectly assumed to be the stop's working rear edge. Example B06 demanded `P04` at `s=11.2566 mm` while `P05` extended far behind the datum, creating **18775.387325 mm³** genuine OCC material overlap. At `X_m=0`, the actual selected stop face spans approximately `-50.6433 <= X_m <= 49.3567`, while the broad rear panel ends are accessible in that X strip. The old search rejected all ten first operations in 2.16 seconds with `CAD_3D_INTERFERENCE` and depth 0. That rejection is valid for the **old fixed-panel mounting**, but is **not proof that no external rear-edge gauge setup exists**.

**New CAD automatic policy:** `resolve_contact_for_bend` calls `solve_accessible_rear_contact` instead of `BackgaugeModel2D.resolve_for_state`. FoldState still uses the authoritative `fixed_panel` to compute bend poses. The CAD solver constructs rigid panel and finite-zone OCC solids, derives exterior rear-depth candidates from their actual placed extents, tries farthest rear candidates first at the explicitly mounted `X_m`, verifies the **real selected Pad.Face6** is within the contact tolerance of the proposed panel, and rejects any intersection with **all** sheet panels/zones at that position. A candidate's `support_panel_id` may differ from the bend's `fixed_panel`; the distinction is recorded in the machine setup and JSONL trace (events `EXTERNAL_REAR_CONTACT_TRIAL` and `EXTERNAL_REAR_CONTACT_SOLVED`). It is **not** treated as implicit permission to bypass collisions. Search, manual replay and the 2.5D CAD preview share the same solver.

**Fail-closed classification:** no accessible exterior contact at the **configured** `X_m` is labeled `CAD_CONTACT_UNAVAILABLE` and prunes this particular setup. This does not prove the part is impossible at another station or with other equipment. Explicit nonzero edge clearance or two independent stop fingers without a matching two-axis external-contact solver are rejected, not simulated with fabricated independent positions. The current implementation **does not automatically relocate the physical stop X axis**: usable machine X travel limits, finger separations, mounting offsets, and axis speeds are not yet authoritative. No continuous loading, transfer, clamp, frame, punch, die or whole-machine swept 3D path is certified. Static OCC viability remains **experimental** until a new local FreeCAD run.

**Validation:** headless regression tests model P04 at 11.2566 mm, long P05 at 508.77 mm, and an external P07 at 548 mm; they verify that the stop selects P07, cannot claim a working-face contact outside its actual X width, and does not bypass an intervening bend-zone solid. A separate FreeCAD-only regression builds actual Part solids, loads real `Backgauge.FCStd`, and verifies Face6 contact plus whole-body clearance; it must run locally because hosted GitHub Actions lacks FreeCAD/Part.

**Required next run:** install the latest experimental branch, set one CAD finger at the same `X_m=0` with `edge_clearance_mm=0`, rerun the 10-bend sample. Upload both the new planar diagnostics JSON and the new CAD JSONL. Inspect the actual `support_panel_id`, `contact_s_mm`, `CAD_CONTACT_UNAVAILABLE` and remaining phase collisions; do not accept a sequence solely because the root gauge setup passes.


## Automatic CAD contact trace for FreeCAD searches

Real CAD mode now writes a **separate, bounded JSON Lines log** automatically
whenever **SMBS -> 2D search** is started with a CAD backgauge enabled:

- **macOS:** `~/Library/Application Support/FreeCAD/v1-1/diagnostics/smbs_backgauge_cad_<timestamp>_<id>.jsonl`
- **Windows:** `%APPDATA%\\FreeCAD\\v1-1\\diagnostics\\smbs_backgauge_cad_<timestamp>_<id>.jsonl` (falling back to `LOCALAPPDATA` when Roaming is unavailable)
- **Linux:** `~/.local/share/FreeCAD/v1-1/diagnostics/`, or `$XDG_DATA_HOME/FreeCAD/v1-1/diagnostics/`

The results dialog shows the **actual complete filename** and has an
**«Открыть папку CAD-лога»** button. The saved regular 2D search JSON now also
contains `backgauge_configuration.cad_trace_path`. The trace file is *not*
embedded in the JSON report: upload **both files** for diagnosis.

Trace events: `TRACE_STARTED`, `SOURCE_CAD_VERIFIED` (all planar faces,
source bounding box and metadata Face3), `BEND_CONTACT_REQUEST`
(actual X station, candidate fixed panel, 3D placement transform),
`CONTACT_GEOMETRY_PRECHECK` (each transformed 3D panel/bend zone),
`CONTACT_BRACKET_RESULT` (computed nonpenetrating contact),
`CONTACT_OBSTRUCTED` (actual BRep overlap volume, the 3D overlap bounding box,
the obstructing panel, individual stop-face distances and any body
protruding in front of Face3), and `BEND_CONTACT_SOLVED`.

The file is incrementally written so partial events survive a search abort.
It has an explicit **1,500 event cap** and `TRACE_TRUNCATED` marker to avoid
unbounded logs. Writing a trace does **not** relax collision checks or certify
a valid manufacturing sequence. FreeCAD-only OCC instrumentation must still
be verified on the user's local machine; headless CI only verifies the
writer contract and related search behavior.


## 2026-11-11 — real CAD stop obstruction at X_m=0 (20261111-060950 diagnostics)

This source report used **one** complete `Backgauge.FCStd` BRep at `X_m=0`, zero `edge_clearance_mm`, no initial feed, and found 12 *unverified* 10-bend exploratory orders. All ten first-step bend records had `OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT`: for B06, selected contact panel `P04` at `required_stop_s_mm=36.2601` is blocked by `P05` at positive OCC volume **18963.980887 mm³**; hint was `s=11.2566`. B08/B07/B05 repeat the same P05 intersection volume, and the post-fixed-panel retreat is consistently close to 25 mm across nearly every bend.

The **25 mm** systematic shift is consistent with CAD source geometry protruding toward the operator ahead of the documented `Face3` datum at source local X=25 mm. A separate, previously measured -X face exists at local X=0 mm (Face5), but it is in a different source Y region. This is evidence of a likely **CAD body mounting/contact-patch mismatch**, not proof that Face5 is the correct working stop or that a valid gauge configuration exists at X_m=0.

**Changes:** include OCC intersection bounding-box and CAD body leading protrusion in contact errors; reclassify this *specific confirmed solid-solid obstruction* as `CAD_3D_INTERFERENCE` and prune that setup from search. Other source/FreeCAD errors remain `UNVERIFIED_2D`. Manual replay uses the same distinction. This prevents invented complete 10-bend candidates made entirely of physically blocked contacts. It does **not** suppress overlap, change mesh tolerances, or certify any configuration.

**Next physical FreeCAD check:** identify which pieces of `Backgauge.FCStd` constitute the actual contact finger vs mounting body, inspect Face3/Face5 and machine X_m span, and ensure the backgauge geometry is physically installed at its intended X_m/Z_m. A full 200-mm-class body can genuinely strike other panels even if its selected contact face is near a fixed panel. Moving to another X station must only be accepted after actual fixed-panel contact and full 3D collision checks. A new report should list `CAD_3D_INTERFERENCE` rather than classify these as unverified candidates.


## 66.json — corrected first-contact search target (2026-11-11)

The latest ten-bend run (`X_m=50 mm`, single CAD finger, `edge_clearance_mm=25`, `initial_feed_preview_mm=1`) reached depth 10 and produced six **UNVERIFIED_2D** coarse candidates, but all ten bends' contacts failed `FIRST_CONTACT_WRONG_PANEL_OR_NO_FIXED_CONTACT`. Example B06 requested fixed `P04` at `hint_s=36.2566`, yet the previous solver retracted to `stop_s=563.2974` and then reported a 527.0407 mm distance from P04. This is **not a valid physical sequence**; 30-second `SEARCH_LIMIT_REACHED` also applies.

Root cause in earlier implementation: its initial contact search bisected against **any** workpiece panel/bend-zone volume. It therefore solved the *rearmost collision of the entire assembly*, which may be hundreds of mm beyond the selected support panel. Checking support-panel proximity only afterward could never repair that incorrect target.

Fix: bracket and bisect contact against the **required fixed panel** only. At that chosen candidate placement, separately run OCC boolean collision against **every panel and bend-zone**. If an unrelated solid obstructs the contact, fail closed with `OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT` including obstructing component and overlap volume. Do **not** retreat to another panel and falsely call the result contact. Verify proximity and operator-facing face on the fixed panel as before. Search's exploratory unverified candidates remain explicitly unverified until FreeCAD validation.

Headless regressions construct synthetic solids to verify that a distant unrelated rear panel no longer moves the contact and that another panel blocking the intended contact is explicitly rejected. They do not validate the real FCStd BRep or machine travel.


## Real first-contact placement correction — 55.json follow-up

**Issue proven by source review:** prior CAD mode copied a 2D edge target measured in the **bend-midpoint cutting plane** (`X_m=desired_midpoint_xm`, normally zero) and positioned the real stop at a different machine station (the reported run used `X_m=50 mm`). It did not verify first contact before Stage B; selected `Face3` is a valid BRep face but may be recessed behind other surfaces.

**Implemented in this experimental branch:**

1. For CAD mode, sample the fixed panel at the *actual configured stop X station* without moving the workpiece's bend reference. Two separate gauge stations must agree in their support-edge target within 0.05 mm; otherwise fail closed until independently driven stop axes are modeled.
2. Keep the current press-aligned workpiece pose. Build the actual OCC panel/bend-zone solids once and position one or two genuine CAD stop bodies. If they overlap, move **the stop bodies rearwards (increasing machine +s)** until there is no material intersection; bracket the first physical collision, then binary-search the nearest **nonpenetrating** contact location. Bounds come from actual sheet/gauge extents, **not guessed machine travel**.
3. Check the final stop body's distance to the *intended fixed panel* and to its real **operator-facing planar surface**; if contact occurs first with another panel, or no operator-facing face can make physical contact, return explicit `BackgaugeCADError` and do not label it collision-free. The system never teleports the sheet through the stop to force the bend.
4. Record `stop_s_mm`, rearward correction `shift_rear_mm`, `actual_faces` and source `datum_face` in the setup note. If the physically encountered surface is **not** metadata `Face3`, mark the step `UNVERIFIED_2D`: this surface has not been qualified as the intended machine stop. Continue independent phase collision tests for diagnostic evidence.
5. Automatic search, manual replay, prefix replay and the 2.5D CAD viewer now request the same actual first-contact solver. They retain normal stage-A/B checks and do not auto-accept a step with unresolved CAD contact.

**Limits:** the first physical contact is a static geometric placement only. The temporary rearward positions used to bracket it are not a claim that machine X/R/Z axes can reach those positions; clamp travel, full station swept paths and tooling safety remain unverified. FreeCAD OCC runtime tests need to be run on the 10-bend benchmark after updating the installation; headless CI does not contain FreeCAD/Part.

**How to interpret follow-up JSON:** `cad_geometry_enabled=true` is still needed. If `machine_setup.note` contains `CAD_FIRST_CONTACT:stop_s_mm=...`, a real nonpenetrating contact was found. Errors `FIRST_CONTACT_WRONG_PANEL_OR_NO_FIXED_CONTACT`, `NO_OPERATOR_FACING_STOP_FACE_CONTACT`, or `NO_PHYSICAL_CONTACT_IN_REAR_GAUGE_TRAVEL` indicate that the chosen CAD stop/finger station is not physically placed against the intended fixed panel. These findings must not be suppressed. A changed `max_depth_reached` is evidence of search progress, not manufacturing approval.


## 55.json — actual CAD stop collision at APPROACH

The ten-bend run with one gauge at `X_m=50 mm` and `initial_feed_preview_mm=100` still has **zero first-step candidates** and `max_depth_reached=0`. Unlike `44.json`, the assumed first-load overlap is preserved as **UNVERIFIED** and Stage B proceeds to `APPROACH`. Every root bend then has positive OCC volume intersection between the actual `Backgauge.FCStd` BRep and a FoldState panel.

Selected first-phase volumes: B05/B07/B08 with P05 ≈16322.206985 mm³; B10 with P01 ≈787.248491 mm³; B06 with P01 ≈280.277396 mm³. Thus the current _single specific CAD mounting_ does not fit the tested first-bend poses. Do not bypass or weaken these intersections to manufacture a sequence.

The previous FreeCAD stop-face audit identified multiple -X-oriented planar faces: Face3 at local X=25 mm, Face5 at local X=0 mm. That means **some source-model material may be ahead of the selected Face3 contact plane**, depending on its actual lateral/vertical extent. Face3 is a valid face, but *not thereby proven to be the leading usable workpiece stop for this entire CAD body*. Other possible blockers are the 200mm-class source CAD axial span and placement at one fixed `X_m=50mm` station versus the actual sheet panel spans. The machine configuration and intended finger contact region need independent verification.

The CAD checker now records per-overlap `gauge_station_xm_mm`, `body_ahead_of_stop_face_mm`, and exact `finger_bbox_xyz_mm` and `panel_bbox_xyz_mm`. The automatic target's configured small clearance is applied consistently in search, manual replay, and CAD section viewer. These measurements DO NOT certify manufacturing clearance; they identify whether the BRep was mounted as intended.

**Next check**: after updating/restarting FreeCAD, rerun the same 10-bend part with the same one-gauge X station. Inspect the new CAD BRep bounding boxes and forward protrusion. Verify the stop face and mounting by inspecting the actual single-finger CAD geometry; do not change the collision threshold or silently relabel Face3 as the frontmost face.

# SMBS 2D automatic backgauge — development status

Branch: `experiment/2d-backgauge-kinematics`, forked from `integration/gui-2d-search-screening`.

## Machine coordinate contract — operator left, interior right (2026-11-10)

All machine-section calculations use the same physical frame:

- Screen/machine `s = -Y_m`; the **bend line, punch working axis and die V centre** are at `s = 0`.
- **Left (`s < 0`)** is the operator side / free portion of the workpiece.
- **Right (`s > 0`)** is the inside of the press / rear-gauge area.
- The active bend's fixed/support panel is physically placed to the rear/right. `WorkpiecePlacement3D.compute_placement(..., rear_panel_vertices_xy=...)` uses a 180-degree rigid rotation about machine Z where needed. No screen-only mirroring; tooling is not flipped or moved off the bend axis.
- The automatic gauge edge is chosen at positive `s` only, and the whole stop rectangle must remain in `s > 0`. Negative manual targets are rejected.
- If geometry leaves both potential rear edges equally plausible, this is `UNVERIFIED` / ambiguous, not a reason to fabricate a side.
- Search Stage B, manual replay, operation setup scoring and live 2.5D press-cycle placements use the same rear-right orientation when backgauge is enabled. The previous negative stop positions and all-first-step collisions in the diagnostic JSON should not recur from the old *side-selection* bug; real physical/projection collisions may remain.
- The punch and die are *stationary machine tooling*: the reference working tip and V groove remain at `s=0`; they are not simply reflected horizontally because their own profile shapes, datum registration and handedness need machine-specific evidence.

**Regression note:** new sampled 2D results are not directly comparable with earlier reports that used ambiguous machine orientation; recalculate from the current version. No claim of production safety or swept-path clearance.

## 44.json — assumed initial-load pose is not a proven mandatory route

The one-finger CAD run at `X_m=50 mm`, with the operator-entered
`initial_feed_preview_mm=100`, successfully invoked real OCC BRep
intersections, but **all ten first-bend candidates** were rejected at
`LOAD_TO_GAUGE`. B05–B10 had the *same* `finger1:panel:P05`
intersection volume (~19152.378444 mm³) at feed fraction `0.0`.
This validates that the CAD solids overlap at the *assumed* initial
preview pose, but does not prove the part cannot be loaded or that every
first bend is infeasible: the starting pose is not derived from a real
operator loading/handling plan, and different initial grips/heights or
machine axes were not explored.

**Implemented change**: retain those 3D intersection measurements in
`diagnostic_findings` and mark the corresponding first operation
`STAGE_B_LOADING_ROUTE_UNVERIFIED -> UNVERIFIED_2D` **if its sampled
bend setup is otherwise clear**. Search can continue exploring the
bend order, but the loading route stays unresolved and its candidate is
never certified as physically collision-free. **Actual collisions in
APPROACH/BENDING/BOTTOM/RETRACT remain blocking**, with no tolerance
widening, collision suppression, or fabricated machine positions.
Any preview/UI gate requiring fully verified steps may still refuse
playback of the unverified result; this is intentional until a real
loading trajectory is specified and checked.

## Real CAD BRep mode — implementation for FreeCAD runtime (2026-11-10)

**Implemented in the experimental branch**:

- In **Задний упор** choose **Использовать реальную 3D-модель Backgauge.FCStd при поиске (экспериментально)**. Specify the position of **one physical CAD gauge unit** along X_m (mm). The setting is opt-in; the old 2D rectangle remains available only as a separate explicitly selected legacy mode.
- The `BackgaugeCAD3D` adapter reads `toollibrary/Backgauge/Backgauge.FCStd` as an actual OCC solid (not metadata BBox), checks the corrected Face3 stop datum and rotates its `(-X)` normal into machine `(+Y_m)` (toward the operator). It positions that contact face on the positive rear edge `s > 0`; no silent source-face substitution.
- Stage B reuses the same rigid machine pose as punch/die, constructs sampled 3D solids for all panels and finite-radius bend zones from `FoldState`, and uses OCC solid Boolean intersections against the actual CAD stop. It reports `BACKGAUGE_CAD_3D` with a panel/zone ID and measured positive overlap volume. Empty sampled intersections do not count as a manufacturing safety certification.
- Initial `LOAD_TO_GAUGE` with explicit positive feed distance is screened at five sampled positions against the CAD stop **on the FIRST operation only**. This is NOT a continuous swept-path collision check and does not test other machine parts during loading.
- Automatic search, full-density candidate replay and manual-order replay use the same CAD Stage B gate. Diagnostics have `geometry_evidence=REAL_BACKGAUGE_FCSTD_SAMPLED_BREP_3D_PANELS_ZONES_NOT_SWEPT` only after the adapter actually loads.
- The 2.5D viewer uses a **slice of the actual positioned CAD BRep** at its configured machine X station. It must not show a synthetic rectangle if the CAD model cannot be loaded.

**Remaining blockers / restrictions**:
1. One CAD stop body at one **explicitly chosen** X station; this does not invent two separate rear-gauge fingers, their mounting offset, limit switches, or machine travel limits. Two independently driven fingers and their placements need a separate model/configuration.
2. CAD sheet solids are sampled from the current BendZone/FoldState geometric model. No proof of swept motion between samples, forming-tool contact dynamics, machine frame, clamps, or actual mechanical finger movement. Do not use results as a production go/no-go without independent verification.
3. FreeCAD runtime tests (`tests/test_backgauge_cad_3d.py`) are only runnable with FreeCAD/Part installed. The authoring environment did not execute FreeCAD or OCC; a local test on the benchmark 10-bend part is still required.
4. FreeCAD GUI settings must explicitly opt into CAD mode; existing saved settings remain in legacy 2D mode until changed.

**Local test**: update the Git checkout, restart FreeCAD, open **Задний упор**, enable the real CAD model, set the desired X station, Save; rerun search and export a fresh JSON. Check that the reported `geometry_evidence` changed and that collisions mention `BACKGAUGE_CAD_3D` rather than `BACKGAUGE`. A malformed source or invalid sheet solid must yield `UNVERIFIED`/an error, never an invented `CLEAR`.

## Correct source model: toollibrary/Backgauge (2026-11-10)

The actual rear stop is **`toollibrary/Backgauge/Backgauge.FCStd`**, accompanied by **`Backgauge.tooling.json`**. It is a different asset from the `Clamp` library entry. Verified sidecar metadata:

- `kind: backgauge`, `id: Backgauge`, `source: Backgauge.FCStd`
- `stop_face`: plane origin `(25,137.5,7)` mm, outward normal `(-1,0,0)`, tolerance `0.05` mm (corrected from invalid source origin `(0,100,7)` after FreeCAD Face3 diagnostics)
- `working_axis`: origin `(0,100,7)` mm, direction `(0,1,0)`, tolerance `0.02` mm
- `mounting_plane`: origin `(0,0,0)`, normal `(0,0,-1)`
- metadata dimensions: length `100` mm, width `200` mm, thickness `14` mm

**Datum fix from FreeCAD diagnostic:** source `Body` has 1 solid and 10 faces (8 planar). The original JSON datum point `(0,100,7)` was 25 mm from Face3. Face3 has outward normal dot +1 relative to `(-1,0,0)`, area 1750 mm² and centre `(25,137.5,7)`. Consequently, only the `stop_face.origin` metadata was corrected to the observed Face3 centre. `working_axis` and source CAD were left unchanged pending separate axis verification. Re-run CAD preflight locally before treating this as a validated contact face. The design may contain multiple rearward-facing faces, so actual use as a gauge finger remains unqualified.

**Sequence-search diagnostic `22.json`:** its `backgauge_configuration.geometry_evidence` explicitly says `2D_RECTANGLE_ONLY_NOT_3D_VERIFIED`, and all 10 root bends still fail Stage B at APPROACH. This metadata fix does NOT alter that algorithm and cannot fix those reported 2D projections. The real CAD model must be connected through a separate 3D stop/finger collision contract; never discard intersections by fiat.

**Source-contract implications:** its contact face is at source-local X=0 and faces -X. In machine section (+s toward rear/right), the physical contact face must face the operator (-s), and the gauge body should remain to the right (+s) of that face. Do NOT treat the source metadata's 200 mm width as the 2D gauge depth or invent a 10x10 mm square; the exact BRep orientation and which solids constitute the actual fingers must be loaded and inspected in FreeCAD.

**Current implementation status (updated):** the actual `Backgauge.FCStd` BRep is available as an **opt-in sampled CAD 3D mode** in Stage B; the legacy rectangle is still available separately. Replacing the rectangle safely requires a FreeCAD geometry adapter that preserves the source object's placement, validates the datum plane against actual BRep faces, registers the stop-face-to-target transform and the X-axis stations of separate fingers, and performs collision checks in 3D (including LOAD_TO_GAUGE and TRANSFER). Until this is implemented, diagnostic evidence must not claim CAD backgauge clearance.

The older `Clamp` notes below are retained for clarity only: **Clamp is not the selected rear-gauge tool**.

## Clamp.FCStd audit and loading approach (2026-11-10)

`toollibrary/Clamp/Clamp.FCStd` is a real FreeCAD model with an associated
`Clamp.tooling.json` sidecar. The checked sidecar declares:

- `kind: clamp` (not yet a certified rear-gauge finger)
- `width_mm=150`, `length_mm=150`, `height_mm=40`
- mounting plane origin `(75,75,40)`, normal `(0,0,1)`
- working axis origin `(75,75,0)`, direction `(1,0,0)`
- working plane origin `(75,75,0)`, normal `(0,0,-1)`

**Do not substitute the bounding box of this part for a rear-gauge finger**.
It may be a workholding clamp rather than a backstop. Its actual BRep shape,
contact face orientation, mounting/stroke constraints and usable finger
positions need verification in FreeCAD. No existing metadata designates a
rear-stop contact face, X/R/Z travel datum or left/right finger stations.

Before using the CAD file as collision geometry, a FreeCAD adapter must:
load the source shape, verify solid validity, derive an explicit backstop
contact face/normal and mounting transform, position independent physical
fingers at machine X stations, and intersect each 3D gauge solid with all
rigid folded panels during both SETUP and the swept first-feed/TRANSFER path.
Only after the datum/geometry check should it replace the provisional
2D rectangle in the physical collision oracle.

## Initial introduction of the workpiece (opt-in preview)

The first operation can now start with `LOAD_TO_GAUGE`, before
`APPROACH`. In the **Задний упор** GUI choose an explicit
`initial_feed_preview_mm` value (0 disables it). During this phase the
sheet moves rigidly from the **operator/left** side (`s<0`) to the
first gauge target (`s>0`); punch and die remain stationary and the
ram is held at its upper position. This is visual only: the default is
**disabled** until the operator chooses a preview feed distance, and the
path has no production-safe collision qualification. The distance is a
preview start offset, **not** an inferred real loading starting position.

The intended full cycle is:
`IDLE -> LOAD_TO_GAUGE -> APPROACH -> CONTACT -> BENDING ->
BOTTOM -> RETRACT -> TRANSFER -> ...`.
The post-bend `TRANSFER` remains separate from the initial sheet feed.

## Target behavior

For every bend, the planner computes the machine-space distance from the active bend line to a supporting **fixed-panel edge** using the current folded state. The backgauge stop is positioned at the selected edge, automatically repositions when the next bend is selected, and moves as an interpolated 2D preview in the `TRANSFER` phase.

The operator **does not need to type an individual target for every bend**. In the **Задний упор** toolbar dialog, enable checks and leave the default **Автоматически вычислять положение по краю опорной панели** checked. Enter the physical stop rectangle dimensions and optional clearance. Manual target entry remains available as an override.

## Geometry and coordinates

- Uses `FoldState` and `WorkpiecePlacement3D` to align the active bend in machine coordinates.
- Uses `compute_machine_cutting_plane_section(...)` to obtain **the active fixed panel's** real cross-section at the bend midpoint.
- Derives a **strictly positive rear/right** edge from that fixed-panel cut polygon only (not overall part bounding box and never the moving flange), after the physical machine orientation has been normalized.
- The measured bend-to-edge depth is `abs(edge_s)` in mm; gauge rectangle centre `s` is offset outside that edge by half the stop width plus optional clearance; `z` is aligned to the edge's thickness centre.
- If no fixed-panel edge can be resolved at the machine cutting plane, the candidate is **UNVERIFIED**; no made-up length.
- Geometry mode is `AUTO_FIXED_PANEL_EDGE_2D_UNVERIFIED`; geometry/station orientation still requires qualification against the actual machine.

## Collision and weight integration

- Automatic and manual 2D search generate a target separately for **each active bend in the already-folded sequence prefix**.
- The existing Stage B 2D polygon-contact oracle checks the stop's *target position* in sampled APPROACH, BENDING, BOTTOM, RETRACT phases.
- `backgauge_move_fixed` and `backgauge_move_per_100_mm` score changeover between successive target (s,z) positions; they do not change collision outcomes.
- Automatic cost and manually entered order cost use the same penalty model, and JSON contains per-step targets, source and penalties.
- The sequence catalog's simulation assignment reruns sampled 2D checks with the current backgauge configuration.

## Visualization

- Gauge outline is drawn in the main 2.5D viewer when configured.
- During the existing `TRANSFER` phase it linearly interpolates stop centre from the previous bend target to the next bend target.
- This **is a visual planning interpolation only**, not physically constrained axis motion and not a validated collision-free transfer.

## Important limitations still open

1. The selected far edge is a 2D geometric candidate, **not yet proof of an accessible physical rear-gauge contact face**; direction of the rear machine side, finger reach, and support stability need explicit machine-specific contracts.
2. Stop represented by one 2D rectangular cutting section, not independent left/right fingers, X/R/Z axes or their 3D geometry.
3. Inter-bend motion is **not collision-checked**, even though its visual interpolation is shown. It can pass through the sheet or tooling.
4. No motor motion constraints, axis stroke limits, coordinated sheet transfer, safety interlocks, finger retraction, staging, or real-time machine control.
5. No automatic recognition of alternate supporting edges or reliable backstop placements for flange-less, heavily folded, tapered or unsupported part profiles.
6. No production certification from any sampled 2D result.

## FreeCAD test protocol

1. Checkout this branch and restart FreeCAD. Load test part `2134`.
2. Open top toolbar **Задний упор**. Enable checks, keep **Автоматически вычислять положение** active, enter stop width/height and save. **Do not manually enter per-bend positions.**
3. In **2D варианты**, check JSON `backgauge_configuration.automatic=true`, `machine_setup.backgauge_s_mm`, `backgauge_z_mm`, `machine_setup.note` containing measured depth and `cost_breakdown[].backgauge_move_cost`.
4. Run manual-order replay with the same sequence and weights; compare scores and backgauge findings.
5. Select a result for simulation. During **TRANSFER**, observe the orange gauge stop moving smoothly. If it moves across the workpiece or into tooling, that is a **known unsupported hazard in the current stage** and must be logged for future motion checking.
6. Confirm that missing fixed-panel edge produces a visible unverified diagnostic, not a nominal valid target. Repeat with a different bend sequence and confirm targets are recalculated based on preceding bends.

The next qualification milestone is machine-axis-calibrated dual-finger contact selection, with swept path collision checks for both the stop and the workpiece during TRANSFER.
