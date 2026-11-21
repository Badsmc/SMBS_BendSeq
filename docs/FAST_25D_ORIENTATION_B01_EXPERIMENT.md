# FAST / legacy 2.5D orientation study: B01 / P11

Branch: experiment/fast-25d-orientation-b01-20261121
Parent: fix/fast-axis-soft-penalty-20261120

## Why this exists

The 10-bend sample reports a possible B01 collision between already-bent P11 and
the selected upper punch after previous bends. FAST already tests yaw 0/180
around machine Z (reverse_axis). The old 2.5D viewer also models a longitudinal
sheet roll. These are different physical operations. The latter can reverse the
contact side of a signed bend, so a clear 2D projection alone cannot validate it.

## Implemented

- fast_orientation_25d.py explicitly enumerates four poses: yaw 0/180 x roll 0/180.
- The longitudinal roll is a proper 3D rigid transform about machine X (det=+1).
- Signed upper-punch contact face is checked; wrong side means INVALID_PUNCH_FACE.
- FAST yaw ranking now includes APPROACH, 50%, 75%, and 100% samples when
  verified die datums permit estimated mid/late-stroke placement.
- The best full candidate's three strongest suspect transitions are diagnosed
  in all four poses. Look for report.orientation_25d_findings in FAST_SEARCH_RESULT.
- Rolled poses are diagnostic only and do not enter the chosen candidate or
  simulator. All findings remain UNVERIFIED_3D_NOT_CHECKED.

## FreeCAD check on the user's part

1. Install this experiment as a complete add-on checkout and restart FreeCAD.
2. Load the same ten-bend FCStd sample; select a GEOMETRY_VERIFIED punch/die pair.
3. Start FAST, then open Desktop/SMBS_debug_log.json.
4. After Stage B's shortlist finishes, inspect the new event
   FAST_25D_ORIENTATION_STAGE_B_DIAGNOSTIC.report.cases.
5. Check the B01 case with completed_mask=31 (B09, B10, B06, B05, B07)
   and also the B01 case with completed_mask=24 (B05, B07).
6. Compare four pose IDs, their raw tool contact, and INVALID_PUNCH_FACE statuses.
7. FAST_SEARCH_RESULT.report.orientation_25d_findings may be empty if the
   best FAST proxy candidate had no suspect transitions. This is expected;
   Stage B's new diagnostic targets the *actual* detected B01 obstruction.

If proxy_tool_available is false, the test did not have real selected tool
contours, and cannot make clearance claims. If the complete candidate is missing,
or its proxy has no suspected collision, orientation_25d_findings may be empty.

Do not inject longitudinal rolls into the candidate/GUI before full 3D
pressing-side, tool support and transfer-path checks are implemented.

## Headless validation

Run: python -m unittest discover -s tests -v
Also run: python tools/run_fast_bfm_beam_benchmark.py
The branch-specific GitHub workflow runs both on every push.
