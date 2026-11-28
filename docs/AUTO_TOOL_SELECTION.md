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
