# One machine pose for FAST, Stage B and 3D simulation

## Scope and current branch

Experimental branch: `experiment/fast-25d-orientation-b01-20261121`.
The existing SheetMetal `FoldState`, `BendZone`, and
`WorkpiecePlacement3D.compute_placement()` remain the authoritative
geometry. No separate fast-search sheet representation is introduced.

The single phase-aware entry point is:

```python
from freecad.bendseq.core.kinematics2d.machine_pose_contract import compute_machine_pose

placement = compute_machine_pose(
    fold_state, bend_id,
    phase="BENDING",                  # e.g. APPROACH, BOTTOM, BEND_ONLY
    support_plane_zm=height_from_actual_stroke,
    reverse_axis=False,               # physical 180-degree yaw around machine Z
    rear_panel_vertices_xy=panel_vertices_xy,
)
# placement.rotation / placement.translation are passed unchanged to
# the 2D machine-section checker or Coin3D sheet mesh transform.
```

For diagnostics use `compute_machine_pose_frame(...).diagnostics()`,
which records the angle, stage, support height, orientation, rear-frame
normalization and actual 3D rotation/translation. The pose is a *rigid
isometry*, never a mirror of a physical sheet panel.

## Integration points

| Consumer | Location | Unified input |
| --- | --- | --- |
| FAST proxy selected-punch/die silhouette | `fast_proxy_tool_screen.py` | APPROACH / BENDING / BOTTOM |
| Stage B selected-tool section collision preview | `preliminary_screening.py` | SETUP / LOAD_TO_GAUGE / APPROACH / BENDING / BOTTOM / RETRACT |
| Native press-cycle 3D playback / frame animation | `press_cycle.py` | current press-cycle phase, assisted retract and manual BEND_ONLY |
| 3D sheet mesh and user scrub | `pressbrake_2d.py` -> `_current_preview_machine_placement()` -> Coin3D | same phase-aware placement |
| OCC collision snapshot overlay comparison | `pressbrake_2d.py` | NATIVE_SAMPLE |
| Next-bend transfer target | `press_cycle.py` | TRANSFER_TARGET |

All of these now route to the **same machine pose function**, rather
than independently choosing `align_bisector`, the arc-center reference,
and loading orientation.

Machine cross-section `s = -Y_m`; X is the active bend axis, Z is
vertical. Machine tools stay stationary; the sheet moves between bends,
and the ram/punch moves vertically for bending.

## Required equality before treating two checks as the same physical pose

1. Identical `FoldState`: signed previously completed bend angles and
   current fractional bend angle.
2. Identical `bend_id` and operation-level `reverse_axis`.
3. Identical phase (APPROACH is *not* the same as BENDING).
4. Identical `support_plane_zm` from the coupled selected-die stroke.
5. Identical `rear_panel_vertices_xy` normalization policy.
6. Identical selected punch/die, ram Z and tool reference datums.

`bend_support_plane_zm(outer_radius,stroke)` is now used by FAST, Stage B,
and 3D preview. FAST also derives effective V opening using the existing
profile-arc-aware helper used by the coupled 2D model.

The software **cannot** claim consistent physical collision results
unless all these inputs match. For example, FAST's simplified tool curves
and Stage B's richer 2.5D sections can still disagree about P11 because
they approximate geometry and tolerate overlap differently. Both remain
sampled advisory checks, not full 3D or transfer-path certification.

## FreeCAD regression: known B01 / P11 case

1. Install only this full experimental branch as a FreeCAD Mod, restart
   FreeCAD and open `four-bend-sequence.FCStd`.
2. Select the same verified punch and die as the previous run.
3. Run FAST; read `FAST_SEARCH_RESULT` and
   `FAST_25D_ORIENTATION_STAGE_B_DIAGNOSTIC` from Desktop's
   `SMBS_debug_log.json`.
4. Apply an unverified FAST candidate (saved FCStd first) and enter 3D
   simulation; inspect the same B01 operation and orientation.
5. Compare `PREVIEW_GEOMETRY_FRAME.rendered_machine_rotation` and
   `rendered_machine_translation_mm` with matching Stage B/FAST geometry.
   If the sampled collision is still inconsistent, verify phase,
   ram stroke, support Z and tool datums before changing search order.
6. Perform a **separate** 3D OCC collision check. Successful sampled FAST
   or 2D preview does not establish safe manufacturability.
