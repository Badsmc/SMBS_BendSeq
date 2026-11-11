# Backgauge CAD contact — Z interval / 2.5D pilot

Branch: `experiment/2d-backgauge-kinematics-cascade`

## Why this exists

The old automatic stop solver used the **middle of an entire panel's 3D
bounding box** as the only candidate stop height. For a 300 mm tall vertical
panel, it might choose Z=150 mm and reject an otherwise accessible low
working-face contact as outside the configured machine axis travel.

## Implemented

1. Read the *actual verified* Pad.Face6 working face in the machine coordinate
   system at datum Z=0. Its relative Z bounds, not the CAD body's 3D height
   centre, define the working contact aperture.
2. Compute the interval of candidate datum heights for which Pad.Face6 and
   each placed panel's Z bounding range overlap; intersect it with
   `backgauge_model.z_min_mm..z_max_mm` (default -20..+20 mm).
3. Generate deterministic sample heights (near machine zero, centre,
   endpoints and interior offsets). This interval is a **broad-phase only**.
4. Sample three X positions inside the actual Pad.Face6 width (mounted
   station and two quarter-width positions). Use each placed 2.5D section's
   local rear edge over the face-height band as a contact-depth proposal.
   Only if *none* of the sections resolves a depth for a panel may the
   original CAD panel bounding box provide **one** fallback proposal.
5. For each proposed (S,Z), verify real CAD Pad.Face6-to-panel distance
   and actual whole-body CAD material overlap with *all* panels and bend zones.
   Do not approve on 2D overlap alone.
6. Preserve up to four distinct statically CAD-checked (S,Z) proposals
   as primary and `alternative_contacts`. The DFS evaluates these as
   separate gauge setups (each with its own phase screening and cost),
   retains the chosen variant index in every PlanarStep, and full-density
   replay rechecks the matching variant. The GUI preview validator
   re-resolves and rechecks the chosen variant, then draws its selected
   coordinates rather than silently showing the primary contact.
7. If the bounded CAD search cannot establish contact, label it
   `CAD_CONTACT_SEARCH_INCOMPLETE` (unverified), not a physically proven
   impossibility. The trial limit is not evidence of no feasible setup.
8. First run a lightweight tooling-only 2D check in the **same
   rear-oriented machine frame**. A confirmed sampled PUNCH/DIE intersection
   avoids expensive CAD gauge resolution; inconclusive results proceed.
   Use a lazy DFS: cheap predicted handling/ergonomic score orders next
   bends, but sibling bends are not all subjected to costly Stage B/OCC
   ahead of exploration. Final scoring uses actual selected stop positions.
9. CAD JSONL trace captures up to 12,000 raw events; on truncation it emits
   aggregate event, rejection, and proposal-source counts. Periodic
   checkpoints preserve totals during longer runs.
10. The preference for a longer operator-side projected reach remains
    a *soft cost*, including for the first bend.

## Important limits (not yet implemented)

- **Full CAD-free per-bend gauge verification is not yet implemented.**
  The 2.5D section is currently a cheap contact-depth proposal; each proposed
  contact still undergoes selected Pad.Face6 and CAD interference validation.
  Replacing this final validation with the 2D projection would risk approving
  contacts at a different machine X position or missing genuine 3D collisions.
- DFS branches over the bounded returned contacts, but the CAD search is
  not exhaustive: contact heights and three X sections are sampled, and
  some physically feasible setups may not appear.
- The operator-side length preference does not yet create and simulate the
  opposite physical loading orientation. Both loading orientations and their
  backgauge/transfer paths require full pose propagation into the GUI replay.
- 2.5D sections and sampled CAD static checks do not certify continuous
  transfer sweeps, first-feed stop contact, or tool end-zone collisions.
- Sampling bounds may miss a feasible contact. Unknown must remain unknown.
  `candidate_trial_limit_reached` reports bounded search, not impossibility.
- Actual speed-up is **not yet established**. Compare against the parent
  branch on the same FCStd, tooling and search budget.

## Verification

GitHub Actions workflow `.github/workflows/backgauge-cad-experiment.yml`
compiles Python code and exercises:
- `tests.test_backgauge_contact_height`
- `tests.test_backgauge_cad_contact_interval`
- `tests.test_backgauge_cad_external_contact`
- `tests.test_operator_side_preference`
- `tests.test_collision_cascade`
- `tests.test_sequence_search_2d`
- `tests.test_backgauge_variants_in_dfs`
- `tests.test_backgauge_cad_trace`
- `tests.test_gui_experiment_integration`

Full FreeCAD runtime test is still necessary. In the test run log, compare
`EXTERNAL_REAR_CONTACT_TRIAL`, `EXTERNAL_REAR_CONTACT_SOLVED`,
`EXTERNAL_REAR_SECTION_UNVERIFIED`, and `CAD_CONTACT_SEARCH_INCOMPLETE`.
