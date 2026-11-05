# D6 Integrated Candidate Planner — Implementation Status

Branch: `d6-integrated-physical-planner` (based on D5 revision `2783dfb658418d68fbc67124a868385a38d806b8`). The stable `2D` branch is unchanged.

## Implemented and connected

- `sequence_search.py`: deterministic forward DFS with backtracking, state fingerprint via completed IDs + previous bend, cache, resource limits. Strict `VERIFIED` only when all required physical scopes pass. Optional partial preview collects a candidate but returns `UNVERIFIED`.
- `physical_step_oracle_25d.py`: each candidate uses a fresh `kinematics2d.FoldState` and independently positioned selected verified punch/die tool profiles. Reuses `WorkpiecePlacement3D`, `CoupledAirBendingKinematics`, `machine_section_3d`, and D5 `check_d51_machine_section`. Samples APPROACH, BENDING fractions, BOTTOM, and RETRACT; retains signed angle, previous completed bends, and original FoldState.
- `sequence_planning.py`: headless entrypoint for the FreeCAD session.
- `freecad_smbs/commands/pressbrake_2d.py`: existing Calculate button enabled when a flat session is available, background candidate search via a pure-Python worker, Qt main-thread result handling. No changes to running D4 animation algorithms. Explicit user confirmation needed to load diagnostic preview order.
- `pressbrake_sequence_d6.json`: diagnostic artifact created in the user's writable PressBrake traces folder, including all checked steps, rejected reasons, source tooling IDs and unverified scopes. Production approval is never inferred from a preview.
- Existing Play All is labeled Demo. It may animate an arbitrary order and is not a guarantee of manufacturability.

## Reused existing components

- `PanelGraph`, `FoldState`, `BendSequenceController.set_bend_order`
- `WorkpiecePlacement3D`, `CoupledAirBendingKinematics`
- Tooling Library `ToolProfile` and `ToolingSetup2D`
- D5 `contact_oracle_2d.py` via `d5_contact_validation.py`
- Legacy backward DFS, A*, and B-Rep `PhysicalValidator` remain unchanged and available, **not** directly callable from `kinematics2d.FoldState`.

## What is not yet physically verified

1. The 2D contour check covers the machine section at X_m=0, **not** the whole length of the bend and not all solid volumes.
2. The sample fractions do **not** prove continuous swept clearance between samples.
3. No physical transfer-trajectory obstacle check for the whole sheet. Rigid continuous TRANSFER is not enough.
4. Backgauge, clamps, frame and nonadjacent-panel self-collision remain unverified.
5. The legacy `PhysicalValidator` is a B-Rep step validator for a DIFFERENT `Core.FoldState` interface. An exact solid-state bridge and machine/tooling placement are prerequisites to its real use. Merely wrapping its method and bypassing required shape state would be false validation.
6. The coupled stroke model uses numeric tooling defaults unless all fields are validated; the new adapter rejects missing critical fields rather than accepting these estimates.
7. The old D5 2D representation has previously produced false collision reports. D6 therefore refuses to declare `NO_FEASIBLE_SEQUENCE` based exclusively on 2D reports.

## Test and FreeCAD acceptance

- GitHub Actions: `.github/workflows/d6-integrated-physical-planner.yml`, unit tests for the DFS, real kinematic state binding, a mocked classification path exercising the actual geometry modules, D5 section checks, and GUI source compilation.
- In FreeCAD, enable the new workbench branch, select the actual SheetMetal part and verified punch/die. Reset all bends to flat. Click **Расчёт**. The search does **not** start Play All. Read the status and the `pressbrake_sequence_d6.json` report. A proposed order is visibly marked *UNVERIFIED* and can be applied for demonstration only after explicit confirmation.
- **Next critical milestone:** create an exact B-Rep backend bridge mapping real panel/bend solids, verified machine/tool solids, poses and transfer sweeps from the same sampled FoldState; then evaluate every candidate for ALL required scopes. Backward DFS may then be reused with forward verification.

Do not mark D6 production-ready or merge into stable 2D until this milestone and user FreeCAD tests succeed.
