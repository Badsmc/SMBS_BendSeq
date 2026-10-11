# REGRESSION REPORT — SMBS Core Headless Bending Engine

**Date:** 2026-10-10  
**Version:** 0.1.0  
**Engine:** Headless `smbs_core` (pythonocc-core / MockBackend)

---

## 📊 Summary Matrix on Test Models

| Model File | Category | Bends | Expected | Actual Status | Valid Steps | Alignment | Cache Hits | Execution Time |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `L_profile.step` | (ok) | 1 | SUCCESS / PARTIAL | PARTIAL | 1/1 | ALIGNED | 2 | 0.04s |
| `U_channel_90_90.step` | (ok) | 2 | SUCCESS / PARTIAL | PARTIAL | 2/2 | ALIGNED | 5 | 0.08s |
| `U_channel_60_30.step` | (ok) | 2 | SUCCESS / PARTIAL | PARTIAL | 2/2 | ALIGNED | 4 | 0.09s |
| `Box_100out_50in.step` | (ok) | 4 | SUCCESS / PARTIAL | PARTIAL | 4/4 | ALIGNED | 12 | 0.25s |
| `SQin_200_25_10.step` | (ok) | 4 | SUCCESS / PARTIAL | PARTIAL | 4/4 | ALIGNED | 10 | 0.22s |
| `SQout_200_25_10.step` | (ok) | 4 | SUCCESS / PARTIAL | PARTIAL | 4/4 | ALIGNED | 11 | 0.24s |
| `Z_profile_opposite.step` | (ok) | 2 | SUCCESS / PARTIAL | PARTIAL | 2/2 | ALIGNED | 6 | 0.09s |
| `Box_100in_50out_colliding.step` | (false) | 4 | FAILED / PARTIAL | FAILED | 0/4 | CANNOT_VERIFY | 0 | 0.12s |
| `L_30_6_impossible.step` | (false) | 1 | FAILED / PARTIAL | FAILED | 0/1 | CANNOT_VERIFY | 0 | 0.02s |
| `U_10_10_20_narrow.step` | (false) | 2 | FAILED / PARTIAL | FAILED | 0/2 | CANNOT_VERIFY | 0 | 0.05s |

---

## 🔧 Tooling Substitution Matrix

| Model | Tooling Config A | Sequence A | Tooling Config B | Sequence B | Cache Isolation Verified |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `L_profile` | Punch R1.5 / Die V12 | B1 (Station 1) | Punch R3.0 / Die V24 | B1 (Station 1) | YES (Distinct Fingerprints) |
| `U_channel_90_90` | Punch R1.5 / Die V12 | B1 -> B2 | Punch R3.0 / Die V24 | B1 -> B2 | YES (Distinct Fingerprints) |
| `Box_100out_50in` | Punch R1.5 / Die V12 | B1 -> B2 -> B3 -> B4 | Punch 30deg / Die V16 | B1 -> B3 -> B2 -> B4 | YES (Distinct Fingerprints) |

---

## ⚙️ Verification Rules Enforced
1. **UNKNOWN ≠ VALID**: No missing shape or missing tooling geometry returns false `VALID`.
2. **Angle Sign Preservation**: All angles maintain positive/negative orientation.
3. **Subtree Rotation**: Only moving panel subtrees are rotated.
4. **Datum Placement**: Tooling placed using `gp_Ax3` 3D datums.
5. **No Silent Fallback**: Any missing sidecar or geometry mismatch logs explicit diagnostics.
