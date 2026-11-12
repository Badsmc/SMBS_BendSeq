# SMBS Manual native machine orientation — acceptance record

Branch: `fix/unified-native-manual-pose-regression`

## What changed
- Explicit Manual operator choice: normal machine loading 0° or 180° around machine Z, applied to **all bends** in the current Manual run.
- The same orientation is passed to sampled 2D Stage B, operation setup, native OCC D6 (APPROACH / CONTACT / BENDING / BOTTOM / RETRACT), and the 3D preview.
- Native 3D placement caches include the chosen orientation; changing it invalidates the previous Manual verdict and collision viewer.
- In Manual, ergonomic rear-gauge auto-reversal is disabled so no implicit second rotation can alter the operator-selected machine pose.
- The developer JSON records per-bend orientation and sampled native poses. Component attribution uses the exact native collision state's transform.
- A sampled clear result remains `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`, never a safety certificate.

## Required FreeCAD 1.1.x / OCC physical acceptance (not runnable in Linux CI)

Use the same document `2-1-3-4-(ok).FCStd`, verified AP1017 punch and AT3015 V12 die, explicit virtual-length setting, and 10 mm end-zone option used in the November 12 diagnostic runs.

1. Close FreeCAD; ensure **only one** install of this branch exists in the paths FreeCAD scans. Confirm the `SMBS_RUNTIME_READY` module path belongs to the expected install. An installed ZIP checkout may report git branch/commit `UNKNOWN`; the module path must still be correct.
2. In Manual, set `B07 → B03`, choose normal 0° and run Verify. Compare first proven collision (P09 against punch, sampled bracket around 50.625°–52.031°) and record all native pose fields.
3. Run the **same sequence** with the explicit 180° checkbox checked. Confirm 2D Stage B and native D6 log `reverse_axis=true`, and that the actual rigid transformation differs by a 180° Z rotation. Do **not** assume the collision disappears.
4. Repeat for `B03 → B07` under 0° and 180°. Report which step blocks and attribute the actual native collision component. A sampled 3D clearance is not full-cycle or transfer clearance.
5. Switch between Manual and other task pages with the backgauge visual preview enabled. Reopen Tasks after closing. Verify there is no covert auto-reversal, stale collision verdict, or loss of state.
6. Re-run the same inputs with virtual tool length **off** and a physically valid assembled tool geometry; compare against the virtual full-bend-length tool. Never treat virtual coverage as evidence that physically installed tool sections provide that reach.

### Safety exclusions
- Backgauge contact, physical support stability, clamps, press frame, and inter-bend TRANSFER path are not certified by this change.
- Manual UI currently provides 0° or 180° for the entire checked order. The core APIs support a per-bend orientation dictionary, but selection of mixed per-step orientations in the UI remains follow-up work.
- No actual FCStd/OCC run was executed by GitHub Actions. Preserve `UNVERIFIED` when requested machine setup or geometric evidence is missing.
- Do not merge the branch into `main` or the installable `smbs` release solely on the basis of headless CI.
