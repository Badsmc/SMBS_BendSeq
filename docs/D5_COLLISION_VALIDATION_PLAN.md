# D5 — Collision validation implementation contract

Baseline: `2D@bc2f94c8f5183af30d8d359c311dc20baabd6d91`
Working branch: `d5-collision-validation`
Status: PLANNED (this document is not an implementation or test pass).

## Scope and invariants

D4-P2.2 Full Cycle animation B01–B08 is an immutable behavior baseline. Collision checking must be read-only with respect to FoldState, bend angles, panel topology, machine placement and timer state. D5 may reject a candidate pose, but must not silently alter the pose to make it valid. Preserve signed bends and tool contact as distinct from geometric penetration.

Every result MUST explicitly carry: status, phase, bend ID, moving object, obstacle ID, minimum clearance or overlap where computable, tolerance, geometry source and verifier mode. Public result statuses: CLEAR, TOUCH, COLLISION, UNVERIFIED. UNVERIFIED is not CLEAR and is not a valid bend sequence.

## Existing components: reuse, do not duplicate

- `smbs_core/kinematics2d/contact_oracle_2d.py`: polygon-interior 2D section checking (holes excluded; boundary contact allowed); available only as cross-section preview, not 3D proof.
- `smbs_core/kinematics2d/tooling_25d.py`: imported/profile tool geometry and coordinate-space transforms.
- `smbs_core/kinematics2d/press_cycle.py`: source of cycle phase and authoritative placement; do not create a parallel motion controller.
- `smbs_core/Physics/collision_detector.py`: existing headless 3D B-Rep collision implementation; requires backend capability and valid solids, not assumed-valid planar compounds.
- `smbs_core/kinematics2d/trace_analyzer.py`: continuity and contact evidence; do not equate its PASS with collision certification.

## Delivery milestones

### D5.1 — Punch and V-die interference (cross-section)

1. Check actual imported solid 2D tool section polygons (including holes), with confirmed machine transforms.
2. Reconstruct the actual finite-thickness workpiece section at each BENDING frame, including previously folded adjacent panels.
3. Explicitly distinguish permitted touch between punch and sheet / die shoulder and sheet from positive-area penetration into tool material.
4. Return UNVERIFIED on missing contours, ambiguous section plane, degenerate polygons, missing tool alignment, unsupported multiple cross-sections or insufficient geometry.
5. Run sample checks at APPROACH, CONTACT, 0/25/50/75/100% BENDING, BOTTOM, RETRACT, and during transfer if any tool overlap is possible.
6. Include stable object IDs and first offending frame/time in trace. Do not reimplement the cross-section oracle; extend it only when tests demonstrate a concrete need.

### D5.2 — Backgauge, clamps and frame

1. Separate real tooling solids from optional preview geometry.
2. Import and place each obstacle in machine coordinates with explicit datum contracts.
3. Check swept trajectory or conservatively bounded intermediate placements; never certify clearance using only start/end frames.
4. Mark missing obstacle geometry as UNVERIFIED for certification mode.

### D5.3 — Workpiece self-collision

1. Use valid thick solids / exact checks for nonadjacent panels; do not treat zero intersection volume of planar faces as CLEAR.
2. Adjacent bend-zone contact is a separate permitted-contact case with tolerances, not a blanket exemption.
3. Identify panel pairs and overlap type.

### D5.4 — Full-cycle verifier and regression

1. Single read-only validation entry point that consumes authoritative fold state, tool setup, pose and phase.
2. Report per frame, per bend and overall status. FAIL on known interference; UNVERIFIED for insufficient evidence.
3. Add positive and negative fixtures: punch touch; punch intrusion; die shoulder touch; die body intrusion; flipped B05/B07 orientation; missing tool; adjacent/nonadjacent panel overlap; gauge collision; transfer collision; signed bends at +/-30,45,60,90 degrees.
4. Guard B01–B08 existing FreeCAD runtime behavior and record its source commit and tooling files.
5. Do not claim manufacturing feasibility from a mere analytic 2D projection; full 3D checking is needed for overhang/side obstacles.

### D5.5 — Planner integration (after D5.1–D5.4)

The sequence planner may only mark a step feasible when all required collision scopes are CLEAR or explicitly permitted TOUCH with proven identities; UNKNOWN/UNVERIFIED are rejected for automatic approval. Keep diagnostic reasons.

## Acceptance gates

- Unit tests exercise both colliding and noncolliding cases and assert no false CLEAR for missing geometry.
- Run FreeCAD integration on B01–B08 with the actual `puancon` and `matrica` tooling profiles.
- No regression to D4-P2.2 playback, signed bend orientation, motion trace or transfers.
- Show nontrivial tool-workpiece interaction in the trace, including an injected known collision that the verifier rejects.
- Document precision/tolerance, geometric coverage and unverified scopes.
- Manual FreeCAD sign-off remains required; CI success alone is insufficient.
