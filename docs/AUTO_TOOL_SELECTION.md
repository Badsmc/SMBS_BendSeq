# Auto Tool Selection

Experimental first increment: a single common punch/die candidate for the loaded
part. Base: multi-station feature branch; no changes to main or develop.

- Read the existing ToolingCatalog and segment inventory without modifying files.
- Use the existing segment assembly planner with full punch coverage.
- Include the current shared-stroke span where available.
- Rank fewer nominal flange warnings, fewer segments and smaller excess length.
- Missing or insufficient stock, failed clamp layout and search limit are explicit.
- A short flange versus full V width is a nominal advisory, not a universal
  manufacturing constraint. Unknown opening and flange dimensions remain unknown.
- The result is always CANDIDATE_UNVERIFIED. It is not a clearance or process proof.
- Cooperative search budget is checked between pairs; individual planner calls
  can overrun it.

Next increments: material and machine limits with sourced parameters, robust
profile/datum screening, shared-station layout and inventory allocation,
FAST and Mesh integration, orientation alternatives, persistent results and PDF.
Test in native FreeCAD before release. No version bump.

## FAST integration and diagnostics

Review up to five inventory candidates with independent FoldState, real selected
profiles and a common physical assembly. Each review uses the existing FAST
(4 s) and sampled Stage B (2 s); ranking preserves uncertain and obstructed
candidates. The 40 s overall target is cooperative, not a hard deadline.
Only the pair and its verified source-CAD layout are applied, not the FAST
sequence. The operator can rerun the normal FAST workflow before Mesh.
Native Qt/CAD work is performed on the GUI thread between event-loop turns;
Stop takes effect between pairs. Inventory work runs off the GUI thread.

Existing Desktop journal, when enabled: AUTO_TOOL_START*, INVENTORY_FILTER,
PAIR_STARTED, PAIR_LAYOUT, PAIR_ERROR, SHORTLIST, REVIEW_STARTED, PROFILE_LOADED,
FAST_STARTED/RESULT, 2D_STARTED/RESULT, REVIEW_RESULT/ERROR, FINISHED,
CANCEL_REQUESTED, STALE and APPLY_*; one run_id connects the events. Reports
include rejected pairs, timings, tracebacks, stock and machine inputs. No upload.

Native span verification and application are logged before and after the CAD load and layout change. Staged bends use physical bend lengths, not synthetic operation IDs. UI acceptance is pending.

## Apply complete candidate and optional Mesh

The selected candidate's operation IDs, locked orientations and actual shared
stroke coverage are checked against its own sequence, not the original order.
Apply commits the tool pair, source-CAD-verified physical layout, and exact
FAST operation order/orientations via the normal FAST application path after
the contextual page closes. Mesh (checked by default, user can disable) starts
only if the same identity is still active. The standard Mesh budget and overlap
preferences are reused. Mesh reports remain sampled and may be incomplete.
No automatic substitution of another pair after a collision; the user can
rerun selection. AUTO_TOOL_APPLY_REQUESTED/STARTED/SEQUENCE_STARTED/RESULT,
FAST_LAYOUT_COVERAGE/REJECTED, APPLY_ERROR/TOOLING_ROLLBACK/ROLLBACK_ERROR and
MESH_STARTED/OPERATION/RESULT/FINISHED/SKIPPED/ERROR share the same run_id.
Earlier description of pair-only application is superseded by this increment.
