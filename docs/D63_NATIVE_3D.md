# D6.3 / D6.3.2 — Native FreeCAD Part 3D Multi-Bend Sheet Assembly

Branch `d6-native-3d`, forked from `d6-freecad-native`. No new dependencies or duplicated bend-angle/K-factor math.

## D6.3.2 Verification and Final Acceptance Result (2026-11-05)

### Multi-Bend 3D Reconstruction Architecture (`build_multi_bend_sheet_solid`)
- Authoritative input: `FoldState` + `PanelBuilder`.
- Panels: All rigid planar faces cut at the finite-width `BendZone` boundaries are extruded by nominal thickness $t$ and transformed into global 3D space using `state.transforms[panel.panel_id]`.
- Bend Zones: Built using `build_bend_zone_solid()` at active intermediate fold angles in the local coordinate frame and transformed by the fixed panel's global pose.
- Assembly: Connected Open CASCADE union (`fuse()`) producing exactly 1 valid, closed OCC BRep solid at all intermediate states without gaps or self-intersections.

### Real FreeCAD 1.1.3 Test Matrix:

1. **`L-(ok).FCStd` (Single-Bend Baseline, B01: +90°)**:
   - 7 sampled fractions ($0\%, 10\%, 25\%, 50\%, 75\%, 90\%, 100\%$).
   - Reconstructed solid: 1 valid closed OCC solid at all 7 fractions.
   - Reference CAD common volume overlap: **$99.9997\%$**.
   - Result: **`PASS_CAD_GEOMETRY`**.

2. **`Z-(ok).FCStd` (Two-Bend Opposite Signs, B01: +90°, B02: −90°)**:
   - Evaluated from Flat $\to$ B01 sequential fractions $\to$ B02 sequential fractions.
   - Reconstructed solid: 1 valid closed OCC solid at all 14 sampled states.
   - Reference CAD Body volume: $22\,649.336143\text{ mm³}$.
   - Reconstructed fully folded volume: $22\,649.170557\text{ mm³}$ (Difference: $-0.165586\text{ mm³}$, exact two-bend chord discretization).
   - Boolean common volume: **$22\,649.139016\text{ mm³}$ ($99.9991\%$ overlap)**.
   - Result: **`PASS_CAD_GEOMETRY`**.

3. **`135-(ok).FCStd` (Two-Bend 45° Angles, B01: +45°, B02: −45°)**:
   - Evaluated across full sequential fractions.
   - Reconstructed solid: 1 valid closed OCC solid at all 14 sampled states.
   - Reference CAD Body volume: $22\,703.347728\text{ mm³}$.
   - Reconstructed fully folded volume: $22\,703.327029\text{ mm³}$ (Difference: $-0.020699\text{ mm³}$, exact chord discretization).
   - Boolean common volume: **$22\,703.323087\text{ mm³}$ ($99.9999\%$ overlap)**.
   - Result: **`PASS_CAD_GEOMETRY`**.

### Multi-Bend CAD-Consistent Volume Gate (`multi_bend_cad_volume_consistency`):
For independent non-overlapping bend zones:
$$V_{\text{expected}}(\{\theta_i\}) = V_{\text{flat}} + \sum_{i=1}^M \left[(0.5 - K_i) \cdot t_i^2 \cdot |\theta_i| \cdot L_i - \epsilon_{\text{disc}}(n_i)\right]$$
Residual between actual FreeCAD OCC reconstructed volume and $V_{\text{expected}}$ across all fixtures and intermediate angles is **$< 10^{-11}\text{ mm³}$**.

### Acceptance Status Rules:
- `PASS_CAD_GEOMETRY`: All intermediate BRep solids valid and closed, multi-bend CAD volume model checks pass, and real CAD reference comparison passes.
- `FAIL_CAD_VOLUME`: Reconstructed volume disagrees with the multi-bend CAD model.
- `FAIL_GEOMETRY`: Invalid, disconnected, open or non-solid BRep.
- `UNVERIFIED_REFERENCE`: Analytical geometry passed, but reference CAD comparison is unavailable.
- `NOT_RUN`: Test not performed.

> [!IMPORTANT]
> A `PASS_CAD_GEOMETRY` verdict certifies geometric CAD model agreement. It does NOT certify machine collision clearance or real plastic forming validation. Physical material-volume conservation (`volume_consistency`) and multi-bend CAD model consistency (`multi_bend_cad_volume_consistency`) remain distinct checks.
