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

## Bounded Mesh alternatives

Opt in with the new checkbox before applying. Try the selected pair first,
then at most two other FAST-reviewed pairs, in rank order. Retry only on a
confirmed collision in the current sequence; incomplete, conditional, failed
or sampled-clear reports stop the chain. A collision does not prove the tool
itself cannot be used with another order. The total application/Mesh target
is 120 seconds, with individual Mesh limits capped by the remaining budget;
native calls are cooperative and may overrun. A failed application stops,
and the last applied setup remains visible (no silent restoration after a
collision). User input/part/library/settings changes invalidate the chain.
The existing Mesh result and collision display are used for the last attempt.
All attempts are correlated with the same run_id and attempt index, including
RETRY_ATTEMPT_STARTED, RETRY_DECISION, RETRY_FINISHED and RETRY_ERROR.
Runtime UI validation remains required; no continuous safety certificate.

## Punch orientation alternatives

Opt in to both normal and reversed 180-degree variants. The top five physical
pairs share the existing cooperative 40 s search budget; not all variants may
be reviewed. Each orientation is an independent FAST/2D result and Mesh retry
identity. Inventory/segment layout is shared; the same physical punch is not
duplicated. Explicit sidecar metadata allow_reverse_punch=false preserves the
library default only. Mounting/force compatibility is not established by this
option. Use session-local profile copies, never save orientation to the library.
FCStd restore accepts the alternate orientation only if the exact saved profile
fingerprint matches; other profile changes still fail closed. Log orientation
request, actual/default values, result and retry orientation under the same
run_id, and restore separately. Native FreeCAD acceptance remains pending.

## Material and preliminary force screening

The Auto Tool Selection task has a user-editable material library (name/grade,
Rm in MPa and required source), explicitly saved in FreeCAD preferences. No
material strength is guessed. Optional nominal machine force is entered in kN.
Thickness and target bend angles come from the bend model. The reference
formula F[kN] = 1.42 Rm t^2 L / (1000 V) is from
https://www.harsle.com/bending-force-calculator/ (air bending, 90 degrees).
Only 90-degree single strokes are screened against declared ratings;
non-90 and staged operations are reference-only. Shared stroke force is the
sum of actually bent lengths, not the outer span including gaps. No force is
inferred from tool length. Optional sidecar metadata keys:
`max_load_kn_per_m` (positive numeric kN/m) and `load_rating_source` (nonempty
manufacturer reference); ambiguous legacy maximum_load is ignored. Unknown
ratings remain warnings. A reference estimate above a declared rating rejects
that candidate, not the tool for every possible job. This is not a press/tool
load certificate: mounting, concentrated/off-center loading, tool geometry,
actual radius, springback, other bending methods remain unverified.
AUTO_TOOL_TECHNOLOGY_PAIR and AUTO_TOOL_TECHNOLOGY_FAST_STROKES record full
inputs, formula, ratings, warnings, estimates and reasons. Material save
and validation errors are recorded too. Per-project technology persistence,
tool rating editor and PDF integration are pending.
