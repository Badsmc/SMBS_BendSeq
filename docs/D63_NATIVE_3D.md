# D6.3 / D6.3.1 — Native FreeCAD Part 3D geometry of one real SheetMetal bend

Branch `d6-native-3d`, forked from `d6-freecad-native`. No new dependencies or duplicated bend-angle/K-factor math.

## D6.3.1 Verification and Final Acceptance Result (2026-11-05)

### Real FreeCAD 1.1.3 Test (`L-(ok).FCStd`):
- Single bend B01 (+90°, 200 mm bend line, 1.5 mm thickness, 1.0 mm inner radius, K=0.4).
- Tested fractions: `0%, 10%, 25%, 50%, 75%, 90%, 100%`.
- Reconstructed solid: Valid, closed, single OCC BRep solid at all 7 angles.
- Overall Verdict: **`PASS_CAD_GEOMETRY`** (Schema `smbs-d631-single-bend-v2`).

### Mathematical Root Cause of Volume Drift:
The volume difference between the flat unfold pattern and the folded state is not an Open CASCADE defect:
- In the flat pattern, bend zone volume is $V_{\text{flat}} = BA \cdot t \cdot L = |\theta| \cdot (r_{\text{in}} + K \cdot t) \cdot t \cdot L$.
- In the folded CAD state, the concentric circular sector has volume $V_{\text{cad}} = |\theta| \cdot (r_{\text{in}} + t/2) \cdot t \cdot L$.
- Therefore:
  $$\Delta V = (0.5 - K) \cdot t^2 \cdot |\theta| \cdot L$$
- In FreeCAD SheetMetal itself:
  $$\text{Volume}(\text{Body.Shape}) - \text{Volume}(\text{Body\_Unfold.Shape}) = +70.685835\text{ mm³}$$
- The polygonal extrusion with $n$ segments introduces a regular chord discretization error:
  $$\epsilon_{\text{disc}} = \frac{1}{2}(r_{\text{out}}^2 - r_{\text{in}}^2) \left[|\theta| - n \sin\left(\frac{|\theta|}{n}\right)\right] \cdot L$$
- At $n = 64$ chords, $\epsilon_{\text{disc}} = 0.082793\text{ mm³}$.
- The reconstructed solid volume matches the analytical CAD model $V_{\text{expected}} = V_{\text{flat}} + \Delta V - \epsilon_{\text{disc}}$ to within **$10^{-11}\text{ mm³}$**.

### Reference CAD Boolean Comparison (`Body.Shape`):
- Authoritative reference volume: `29324.668072 mm³`
- Reconstructed volume ($n=64$): `29324.585278 mm³` (diff: `0.082793 mm³` exact discretization)
- Reconstructed volume ($n=128$): `29324.647373 mm³` (diff: `0.020699 mm³` exact discretization)
- Boolean Common Volume (`shifted.common(body_shape)`): `29324.569508 mm³` ($99.9997\%$ overlap)
- Cut `Body - Recon`: `0.098563 mm³` (chord residual)
- Cut `Recon - Body`: `0.015770 mm³`

### Acceptance Status Rules:
- `PASS_CAD_GEOMETRY`: All BRep solids valid and closed, CAD volume model checks pass, and real CAD reference comparison passes.
- `FAIL_CAD_VOLUME`: Reconstructed volume disagrees with the expected CAD model.
- `FAIL_GEOMETRY`: Invalid, disconnected, open or non-solid BRep.
- `UNVERIFIED_REFERENCE`: Analytical geometry passed, but reference CAD comparison is unavailable.
- `NOT_RUN`: Test not performed.

> [!IMPORTANT]
> A `PASS_CAD_GEOMETRY` verdict certifies geometric CAD model agreement. It does NOT certify machine collision clearance or real plastic forming validation. Physical material-volume conservation (`volume_consistency`) and CAD model consistency (`cad_volume_consistency`) remain distinct checks.
