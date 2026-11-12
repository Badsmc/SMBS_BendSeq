# Manual 3D collision forensics — B07 → B03

## Evidence from the uploaded developer journal (12 November 2026)

Part: `2-1-3-4-(ok).FCStd`, 11 rigid panels, 10 bends, thickness
1.5 mm, inner radius 1.0 mm. Selected manual sequence begins
`B07 → B03`. Chosen punch: `PUNCH_AP_1017_88°_H104,65_R0,8`.
Chosen die: `T-DIE_AT_3015_88°_V12_H80`. Virtual tool length enabled;
end-zone conditional mode requested (10 mm).

- **B07:** sampled native D6.3.4 clear (49 samples); full RETRACT
  sampled and tool-pair clearance clear.
- **B03:** APPROACH/CONTACT clear. The old validator evaluated
  BENDING at 0° and at 90°; **the 90° endpoint penetrated the punch
  with 4643.374 mm³**; die overlap 0. The adaptive validator stopped
  without examining intermediate bend angles and without running
  BOTTOM or RETRACT.
- Punch common solid at B03 90° spans approximately
  machine X [-248.750,+248.750], Y [-13.150,+0.010],
  Z [-3.493,+16.599] mm. These are **the full common-solid limits**;
  individual intersection components may occupy smaller regions.
- Native punch end-zone classification at 90° was
  `MIXED_CENTRAL_AND_END_ZONE`, so the 10-mm conditional end
  allowance must NOT exempt the central collision.
- Stage A/B sampled 2D approved B03 despite the native 3D collision.
  The center section cannot see 3D flange interference at a remote
  axial station. The previously folded **B07 moving panel subtree
  includes P09**. This makes P09 a plausible culprit, but it was
  *not proved* by the original log alone.

The exact earliest physical collision angle and exact panel label
are **not established by the original log**. This change adds native
diagnostics to test those hypotheses during a new FreeCAD run.

## New diagnostic path

1. Native D6.3.4 retains early rejection of physically blocking
   collisions. When an endpoint is blocking and the start is not, it
   performs up to six extra native collision evaluations in the
   parameter interval (default 30-s diagnostic time cap).
   `first_collision_bracket` records last sampled nonblocking angle,
   first sampled blocking angle, corresponding ram Zs and probe count.
   **No continuous-path clearance or globally earliest contact is
   claimed**; non-monotonic contact can exist between samples.
2. After a proven punch/die-to-sheet collision, a **separate, isolated
   FoldState** reconstructs each real rigid source panel prism
   and bend-zone solid, applies the *exact D6 collision machine
   placement*, and checks actual common volumes against the
   selected real `*.FCStd` tool geometry. Positive components
   are named e.g. `panel:P09` or `bend_zone:B07`.
   The diagnostics use AABB rejection before expensive OCC
   and a diagnostic budget; partial results must say `PARTIAL`
   and must never be passed off as full panel attribution.
3. `first_blocker` distinguishes the earliest sampled collision
   position and **its** per-tool overlap/bbox from the maximum
   penetration over all checked states (often at the 90° endpoint).
4. In **Manual**, the new button
   **«Показать коллизию в 3D»** reconstructs that sampled D6
   machine pose in a separate FoldState, so it does not confuse
   the ergonomic reverse-axis 2.5D camera with validated geometry.
   The impacted known panel gets a contrasting outline. Red
   wireframe shows the **exact common-solid bounding box**
   in machine XYZ — not the shape of the actual intersection.
   Pressing the button again restores the normal 3D view.
5. Stage A/B marks completed bends' moving panels as
   `off_section_3d_risk` rather than claiming a sampled 2D
   pass proves safety. These are **warnings, not 3D collision
   predictions**. Manual and automatic physical decisions still
   require native 3D.
6. The Desktop `SMBS_debug_log.json` records
   `MANUAL_FLAT_BEND_CLICK`, `MANUAL_FLAT_BEND_ACCEPTED`,
   `MANUAL_NATIVE_COMPONENT_ATTRIBUTION`,
   `MANUAL_NATIVE_BEND_TRAJECTORY` (now including the
   onset bracket) and `MANUAL_COLLISION_3D_INSPECTION`.
   Per-bend summaries, brackets, component IDs and provenance
   also save inside `SMBS_Data/SessionJSON` in the same FCStd.
   Existing backgauge visual-only policy and manual simulation
   without checking remain unchanged.

## FreeCAD acceptance checklist

- [ ] Install `fix/manual-collision-component-angle-diagnostics`, open
  the same FCStd, select the same punch and die and enable virtual
  length and 10-mm conditional end-zone mode.
- [ ] Select B07 → B03 manually. Ensure clicks are present in the
  Desktop JSON with exact accepted order.
- [ ] Press «Проверить». B07 should remain sampled clear for the
  unchanged setup. For B03 inspect the **actual** reported
  onset bracket, component list, machine XYZ overlap and
  status. These are new measurements, not values predicted
  in this document.
- [ ] Press «Показать коллизию в 3D». Ensure the scene displays
  the *native D6 pose* (not reversed ergonomic preview), with
  red common-solid bbox. Check P09 is highlighted **only if
  native OCC actually attributed positive volume to P09**.
- [ ] Switch back to ordinary 3D, check wheel zoom, orbit,
  2.5D, Manual, saved sequence and task close/reopen.
- [ ] Save and reopen FCStd; inspect
  `diagnostics.manual.manual_3d_validation.steps[*].first_blocker`
  for `sampled_onset_bracket` and `component_attribution`.
- [ ] Share Desktop `SMBS_debug_log.json` if the result
  remains blocked or attribution returns partial/unknown.

**Limits:** no continuous swept collision certification, backgauge
collision checks, transfer, clamps, machine frame, force, or safe
production sign-off. Real FreeCAD/OCC model tests are still needed.
