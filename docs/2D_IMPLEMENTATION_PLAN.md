# SMBS BendSeq — 2D emulator implementation plan

Branch: `2D`. This is an experimental, isolated implementation, not a production-ready press brake simulator.

## Existing implementation

- `smbs_core/kinematics2d/engine.py`: immutable 2D segments and states, signed-angle rigid rotations about an explicit pivot, detection of candidate segment intersections.
- `tests/test_kinematics2d.py`: tests for positive/negative angles, immutability, double application, candidate intersections and invalid panel IDs.
- No FreeCAD GUI wiring yet. No 3D verification yet. No claims of valid bend sequence.

## Critical modelling boundaries

- A single 2D cross-section is a zero-thickness **centre-line model**. It does not represent bend radius, material thickness, swept tool volume, bends along differing axes, backgauge or press collisions.
- Shared endpoints at legitimate hinges appear as intersection candidates. Do not label them INVALID without contact classification.
- For branched parts, moving_panels must be derived from a validated canonical PanelGraph, never guessed from drawing order.
- No 2D result may set production_eligible=true. Full 3D physical validation and forward replay remain mandatory.

## Next implementation milestones

1. Extract validated cross-section from SheetMetal canonical bends; explicit support/UNKNOWN for non-coplanar axes.
2. Extend panels to thickness-aware 2D polygons and curved bend regions, preserving immutable transforms.
3. Implement 2D punch/die profiles, permitted contact and swept-path collision checks with explicit tolerances.
4. Add fast deterministic backward search on this state with resource budgets and rejection reason codes.
5. Wire FreeCAD task panel: profile, tool, active bend, step slider, manual selection and diagnostics.
6. Route complete candidates through existing 3D PhysicalValidator and IndependentVerifier.
7. Test against L/U/C and opposite ±45° FCStd fixtures, benchmark vs OCC-heavy search, only then choose default backend.

Do not duplicate the existing SheetMetal adapter, PhysicalValidator, Coordinator or export schemas.
