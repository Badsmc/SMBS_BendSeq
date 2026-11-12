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

## Live FreeCAD evidence: 2026-11-12 (SMBS_debug_log(5).json)

- Runtime module: `~/Library/Application Support/FreeCAD/Mod/SMBS_BendSeq-fix-unified-native-manual-pose-regression` (branch/commit metadata UNKNOWN from installed package; verify module path).
- Input: ten bends, virtual AP1017 and AT3015 V12 to active bend length, 10 mm conditional end zones. Manual order `B07, B03, B05, B01, B08, B04, B09, B10, B06, B02`.
- Manual 0°: B07 sampled-clear; B03 PROVEN punch collision, P09 component, first sampled blocking angle 52.03125°; physically rejects this exact pose.
- Manual 180°: B07 and B03 sampled-clear in native D6. The 2D Stage B advisory reported a conflicting P09 penetration at B03, which **native sampled OCC did not reproduce**. This is independent evidence disagreement, not proof that either engine certifies continuous safe motion.
- B05 at 180°: `BENDING=INTERVAL_UNRESOLVED` after 27 BENDING samples, 124.24 s against configured 120 s phase limit; 2 unresolved intervals, one starting at phase parameter u=0.75. Max sampled punch common was ~0.000098 mm³ versus 0.0001 mm³ volumetric threshold. No proven blocking collision was established at B05; remaining intervals must be checked.
- Subsequent code change increases **Manual only** limits to 300 s and 64 samples per phase; the new configuration has NOT been run in native FreeCAD, and it may still return UNVERIFIED.
- Structured per-bend `planar_native_discrepancies` now records whether 2D claimed a collision not reproduced by sampled native D6 or native D6 found a collision missed by 2D; this is diagnostic and does not waive any checks.
- Headless GitHub tests verify orchestration, interfaces and safety statuses only. No claim of a completed valid ten-bend plan, continuous safety, verified gauge contact, transfer clearance, or installed tool section geometry.
