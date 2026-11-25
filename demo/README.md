# Demo parts

These models are geometry examples, not certified manufacturing plans. Names containing `expected-blocked` preserve historical collision examples; results depend on the selected tooling and setup. `alternate-punch` identifies a model originally used with another punch.

| File | Previous filename |
| --- | --- |
| `angle-135.FCStd` | `135-(ok).FCStd` |
| `four-bend-sequence.FCStd` | `2-1-3-4-(ok).FCStd` |
| `l-profile.FCStd` | `L-(ok).FCStd` |
| `l-profile-30-6-expected-blocked.FCStd` | `L-30-6-(false).FCStd` |
| `square-inward-200-25-10.FCStd` | `SQin.200-25-10-(ok).FCStd` |
| `square-outward-200-25-10.FCStd` | `SQout.200-25-10-(ok).FCStd` |
| `u-profile-10-10-20-expected-blocked.FCStd` | `U-10-10-20-(false).FCStd` |
| `u-profile-10-15-10-alternate-punch.FCStd` | `U-10-15-10-(punch2-OK).FCStd` |
| `u-profile-10-30-10.FCStd` | `U-10-30-10-(OK).FCStd` |
| `u-profile-60deg-30deg.FCStd` | `U-60deg-30deg-(ok).FCStd` |
| `u-profile-90deg-90deg.FCStd` | `U-90deg-90deg-(ok).FCStd` |
| `z-profile.FCStd` | `Z-(ok).FCStd` |
| `z-profile-10-20-10-expected-blocked.FCStd` | `Z-10-20-10-(false).FCStd` |
| `z-profile-10-20-10.FCStd` | `Z-10-20-10-(ok).FCStd` |
| `box-100-inward-50-outward-expected-blocked.FCStd` | `box.100in-50out-(false).FCStd` |
| `box-100-outward-50-inward.FCStd` | `box.100out-50in-(ok).FCStd` |

## Historical reference data

`SMBendTests/` contains the JSON reference outputs from the earlier main baseline. They use the old filenames and schemas; use the table above to map original part names to current models. They are not validated expected results for the current engine.

`legacy-tooling/` retains only non-punch/die fixture metadata. Historical punch/die geometry and tooling sets have been removed. Historical bend sequence JSON records describe operations, not tooling model geometry. The old executable batch runner and complete legacy implementation remain in the original main history.
