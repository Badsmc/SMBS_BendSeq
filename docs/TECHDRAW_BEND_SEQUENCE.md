# SMBS — TechDraw bend-sequence production sheet

Status: **PHASE 1 PARTLY IMPLEMENTED; TECHDRAW GUI/PDF NOT IMPLEMENTED OR VERIFIED**. This is a mandatory release requirement of SMBS, not a new independent solver.

## User workflow

1. Open a FreeCAD SheetMetal part, including a usable unfold with authoritative bend-line sketch.
2. Press **Find Sequence**. Run the existing 2.5D/3D screening and DFS planner. If no sequence is verified, report the limitations rather than invent an order.
3. Preview the sequence and simulate the bends.
4. Press **Create Bend Drawing** (enabled only when an accepted sequence and reliable bend-line mapping exist).
5. Choose or reuse a TechDraw page and template (A4/A3; portrait/landscape), and add: (a) flattened view with bend lines and order labels, (b) folded-part view, (c) bend operation table.
6. Export through native TechDraw printing/PDF; allow page edits in FreeCAD.

## Canonical numbering contract

- The **single source of sequence** is the accepted planner's ordered stable bend IDs (e.g., `B03, B01, B02` maps to operation `1,2,3`). NEVER number by the CAD object's lexical name, creation order, or GUI row position.
- The **single source of fold/unfold geometry and bend attributes** is the SheetMetal workbench's current unfold, bend line sketch, signed angle, length, thickness and K-factor. No second geometric unfold engine.
- Map each stable bend ID to an actual 2D bend-line segment through the existing bend-to-sketch mapping; validate endpoints, axis alignment and tolerance. Do not assign an operation number when mapping is missing, ambiguous, duplicated, or out of tolerance.
- A bend that must be executed in multiple stations or passes needs explicit operations. Never silently label it as a single press stroke.
- Persist an explicit and versioned `BendDrawingPlan` data model: document/source IDs, source geometry signatures, accepted ordered bend IDs, mapping provenance, 2D endpoints, signed angle, optional operation metadata, layout choices, and verification status.
- Keep the snapshot traceable and detect stale data if the part/unfold, mapping, or accepted sequence changes. Offer **Update Drawing**; do not silently leave outdated labels.

## Printed layout

- At minimum: title block with part identifier / revision / material / thickness / scale / date, flat pattern boundary, all bend lines, sequential `1,2,...` near their respective lines, each signed angle (`+90°`, `−90°`) with orientation legend, basic folded-part view, bend table (`step | stable ID | angle | length | direction | status`).
- Number markers shall be legible in monochrome printing and at final sheet scale; offset markers to avoid hiding lines and dimensions; use leader lines where necessary. Avoid overlap with each other, bend geometry, dimensions, and title block.
- Explicitly differentiate bend-up/bend-down with legend and signed angles. If SheetMetal sign conventions and physical press direction differ, document their reference frame and do not silently invert signs.
- Prefer existing `TechDraw::DrawPage`, `TechDraw::DrawSVGTemplate`, `TechDraw::DrawViewPart` and `TechDraw::DrawViewDraft`; use the existing unfold 2D sketch as source. Text/leader annotations should use native TechDraw/Draft primitives where reliable; SVG overlays must be owned by the page and regenerated from canonical plan, not a one-time screenshot.
- Page and view coordinates must be transformed consistently: model XY -> view projection -> page with scaling, rotation and translation. Never position operation numbers using raw SVG pixel coordinates unrelated to the actual view.
- Do not edit destructive user-owned drawing objects; namespace generated objects `SMBS_*` and update them idempotently. Preserve user dimensions and manual changes when possible, with explicit stale/rebuild indicators.

## Commands and enablement

- **Find Sequence**: active when valid part/unfold selection exists.
- **Simulate Sequence**: active when an accepted ordering is present.
- **Create Bend Drawing**: active when an accepted ordering, authoritative mapped lines, and compatible page/template exist or can be created.
- **Update Drawing**: active when an existing SMBS drawing page exists; on changed inputs, offer review, rebuild, or cancellation.
- **Export PDF / Print**: use native TechDraw; reject export as a verified manufacturing instruction if drawing is stale or missing required operations.

## Acceptance tests

1. L part (one bend), Z part (two bends with opposite signs), U part, box (many bends) and 10-bend demo part.
2. IDs order `B03 -> B01 -> B02` is printed as `1,2,3`, even when CAD construction order differs.
3. Validate bend-sketch mapping including known small geometric offsets; require explicit tolerance and report ambiguous cases.
4. Refresh after sequence reordering, model recompute, page scale, page translation, view rotation and template switch.
5. No duplicated labels after repeat generation or updates; no stale numbers from previously accepted sequence.
6. Source document remains unchanged except explicitly created drawing objects; preserve existing TechDraw content.
7. PDF and monochrome printed output visually checked at A4 and A3 with clear angle signs and table.
8. Feature must work with and without an existing TechDraw page. Clean FreeCAD 1.1.x installation and rerun acceptance required.

## Implementation order

**Phase 1**: accepted sequence and canonical bend-line map -> pure `BendDrawingPlan`; regression tests.

**Phase 2**: idempotent TechDraw page / view generator, reference-frame handling and operation markers; real FreeCAD GUI acceptance.

**Phase 3**: bend table, folded view, PDF/print, update/stale checks and physical printed sheet evaluation.

Do not build a separate CAD engine, manually number unrelated sketch geometry, or mark the feature production-ready based only on the existence of GUI code.

References: FreeCAD official TechDraw Workbench, TechDraw DraftView and TechDraw Templates documentation.


## Phase 1 implementation (2026-11-09)

- `smbs_core/kinematics2d/bend_drawing_plan.py` now provides `create_bend_drawing_plan(unfold, accepted_sequence, source_document, accepted_status, verified)` and immutable, JSON-serializable `BendDrawingPlan`.
- The plan uses `UnfoldData2D.bends`, `UnfoldData2D.sketch_bend_lines_xy` and the **existing** `match_bend_axes` geometric matcher. A missing/ambiguous sketch assignment, incomplete sequence, duplicate bend ID or unverified sequence fails closed.
- Operations are numbered by accepted planner order, not bend/sketch enumeration order. Explicit signed angles are taken from SheetMetal.
- The plan includes a deterministic metadata-and-bend-sketch fingerprint; `is_bend_drawing_plan_current` detects changes in those inputs. **Limitation:** it does not yet hash the whole CAD shape or FreeCAD document revision; downstream TechDraw should additionally track source document recompute/signature and view transforms before printing.
- `tests/test_bend_drawing_plan.py` covers ordering, mapping, duplicate/unknown bends, stale inputs and JSON conversion.
- **Not yet connected** to a FreeCAD GUI button, TechDraw::DrawPage or PDF printing. No claim of printed-sheet acceptance. Until a verified sequence exists, the production drawing action should remain unavailable.
