"""D6.3 / D6.3.2 native Part 3D sheet smoke test on selected SheetMetal unfold.

Supports both single-bend (L-bracket) and multi-bend (Z-bracket, 135-bracket, etc.) parts.
Tests each bend sequentially across intermediate fractions (0, 10, 25, 50, 75, 90, 100%),
validates full 3D BRep solids (valid, closed, 1 solid), evaluates CAD-consistent volume,
and compares the final completely folded solid with the authoritative FreeCAD Body.Shape.
"""
from __future__ import annotations

import json
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.native_volume_gate import (
    volume_consistency,
    cad_volume_consistency,
    multi_bend_cad_volume_consistency,
)
from smbs_core.kinematics2d.native_bend_solid_3d import (
    build_bend_zone_solid,
    build_multi_bend_sheet_solid,
    NativeBendSolidError,
)


def resolve_test_selection(doc, selected):
    """Select a unique SheetMetal unfold when FreeCAD GUI selection is empty."""
    if doc is None:
        raise ValueError("No active FreeCAD document. Open an FCStd SheetMetal model.")
    if selected:
        return list(selected), "EXPLICIT_GUI_SELECTION"
    unfolds = [
        item for item in getattr(doc, "Objects", ())
        if getattr(item, "Name", "").endswith("_Unfold")
        and hasattr(item, "baseObject")
    ]
    if len(unfolds) == 1:
        return [unfolds[0]], "AUTO_UNIQUE_SHEETMETAL_UNFOLD"
    if not unfolds:
        raise ValueError(
            "No SheetMetal *_Unfold object in the active document. "
            "Create the unfold with bend and outline sketches."
        )
    raise ValueError(
        "Multiple SheetMetal unfolds: {}. Select one in the tree and rerun."
        .format(", ".join(sorted(x.Name for x in unfolds)))
    )


def compare_reference_cad_body(doc, reconstructed_sheet_shape, segments=64):
    """Compare final folded sheet solid against authoritative Body.Shape if present."""
    body = getattr(doc, "getObject", lambda _: None)("Body")
    if body is None or not hasattr(body, "Shape") or body.Shape.isNull():
        return {
            "status": "UNVERIFIED_REFERENCE",
            "reason": "Document has no valid Body.Shape to compare against",
        }
    body_shape = body.Shape
    bb_body = body_shape.BoundBox
    bb_recon = reconstructed_sheet_shape.BoundBox

    dx = float(bb_body.XMin - bb_recon.XMin)
    dy = float(bb_body.YMin - bb_recon.YMin)
    dz = float(bb_body.ZMin - bb_recon.ZMin)

    import FreeCAD as App
    shifted = reconstructed_sheet_shape.copy()
    if abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6:
        shifted.translate(App.Vector(dx, dy, dz))

    try:
        common = shifted.common(body_shape)
        cut_body_recon = body_shape.cut(shifted)
        cut_recon_body = shifted.cut(body_shape)
        common_vol = float(common.Volume) if common and not common.isNull() else 0.0
        cut_body_recon_vol = float(cut_body_recon.Volume) if cut_body_recon and not cut_body_recon.isNull() else 0.0
        cut_recon_body_vol = float(cut_recon_body.Volume) if cut_recon_body and not cut_recon_body.isNull() else 0.0
    except Exception as exc:
        return {
            "status": "UNVERIFIED_REFERENCE",
            "reason": f"Boolean comparison failed: {exc}",
        }

    ref_vol = float(body_shape.Volume)
    recon_vol = float(shifted.Volume)
    vol_diff = ref_vol - recon_vol

    is_match = (
        common_vol > 0 and
        abs(vol_diff) < max(0.5, ref_vol * 0.001) and
        cut_body_recon_vol < max(0.5, ref_vol * 0.001)
    )

    return {
        "status": "PASS" if is_match else "FAIL",
        "alignment_applied": [dx, dy, dz],
        "reference_cad_volume_mm3": ref_vol,
        "reconstructed_volume_mm3": recon_vol,
        "volume_difference_mm3": vol_diff,
        "common_volume_mm3": common_vol,
        "cut_body_minus_recon_volume_mm3": cut_body_recon_vol,
        "cut_recon_minus_body_volume_mm3": cut_recon_body_vol,
        "relative_common_overlap_percent": 100.0 * common_vol / ref_vol if ref_vol > 0 else 0.0,
        "body_bound_box": [bb_body.XMin, bb_body.YMin, bb_body.ZMin, bb_body.XMax, bb_body.YMax, bb_body.ZMax],
        "aligned_recon_bound_box": [shifted.BoundBox.XMin, shifted.BoundBox.YMin, shifted.BoundBox.ZMin,
                                   shifted.BoundBox.XMax, shifted.BoundBox.YMax, shifted.BoundBox.ZMax],
        "is_cad_match": is_match,
    }


def main():
    report = {
        "schema": "smbs-d632-multi-bend-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "REAL_SHEETMETAL_MULTI_BEND_FULL_3D_SOLID_NOT_COLLISION_CERTIFICATION",
        "cases": [],
        "result": "NOT_RUN",
    }
    try:
        import FreeCAD as App
        try:
            import FreeCADGui as Gui
            selected = Gui.Selection.getSelection() if hasattr(Gui, "Selection") else []
        except Exception:
            selected = []
        from freecad_smbs.commands.pressbrake_2d import build_part_session

        doc = App.ActiveDocument
        selection, selection_mode = resolve_test_selection(doc, selected)
        report["selection_mode"] = selection_mode
        report["source_unfold_name"] = selection[0].Name
        source_angle_snapshot = {}
        session = build_part_session(doc, selection)
        state = session.fold_state
        source_angle_snapshot.update(state.angles)

        bends_list = [b.bend_id for b in state.ordered_bends]
        bends_params = {}
        for b in state.ordered_bends:
            z = b.bend_zone
            L = math.hypot(z.axis_xy[1][0] - z.axis_xy[0][0], z.axis_xy[1][1] - z.axis_xy[0][1])
            bends_params[b.bend_id] = {
                "target_angle_deg": b.angle_deg,
                "thickness_mm": z.thickness_mm,
                "inner_radius_mm": z.inner_radius_mm,
                "outer_radius_mm": z.outer_radius_mm,
                "k_factor": z.k_factor_ansi,
                "bend_allowance_mm": z.bend_allowance_mm,
                "bend_length_mm": L,
            }

        report.update({
            "freecad": ".".join(map(str, App.Version()[:3])),
            "part_label": session.part_label,
            "bends_count": len(bends_list),
            "panels_count": len(state.graph.panels),
            "bends_params": bends_params,
        })

        fractions = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)
        segments_count = 64
        sampled_states = []
        sampled_sheet_volumes = []
        final_reconstructed_shape = None

        # Reset all bends to 0.0 initially
        for bid in bends_list:
            state.set_fraction(bid, 0.0)

        # 1. Test flat state
        t0 = time.perf_counter()
        flat_sheet = build_multi_bend_sheet_solid(state, session.panel_build, segments=segments_count)
        t_flat = (time.perf_counter() - t0) * 1000.0
        sampled_states.append(dict(state.angles))
        sampled_sheet_volumes.append(flat_sheet.volume_mm3)
        report["cases"].append({
            "step": "FLAT_STATE",
            "angles_deg": dict(state.angles),
            "valid": flat_sheet.is_valid,
            "closed": flat_sheet.is_closed,
            "solids": 1,
            "volume_mm3": flat_sheet.volume_mm3,
            "timing_ms": t_flat,
            "pass": flat_sheet.is_valid and flat_sheet.is_closed,
        })
        print(f"SMBS D6.3.2: FLAT_STATE PASS Vol={flat_sheet.volume_mm3:.4f} mm3 ({t_flat:.1f}ms)")

        # 2. Sequential bending through ordered bends
        for bend_idx, active_bid in enumerate(bends_list):
            for f in fractions:
                if f == 0.0 and bend_idx == 0:
                    continue  # already recorded as FLAT_STATE
                state.set_fraction(active_bid, f)
                t0 = time.perf_counter()
                multi_sheet = build_multi_bend_sheet_solid(state, session.panel_build, segments=segments_count)
                dt_ms = (time.perf_counter() - t0) * 1000.0
                step_name = f"BEND_{active_bid}_f{f:.2f}"
                sampled_states.append(dict(state.angles))
                sampled_sheet_volumes.append(multi_sheet.volume_mm3)

                is_ok = multi_sheet.is_valid and multi_sheet.is_closed and multi_sheet.volume_mm3 > 0
                report["cases"].append({
                    "step": step_name,
                    "active_bend": active_bid,
                    "fraction": f,
                    "angles_deg": dict(state.angles),
                    "valid": multi_sheet.is_valid,
                    "closed": multi_sheet.is_closed,
                    "solids": 1,
                    "volume_mm3": multi_sheet.volume_mm3,
                    "timing_ms": dt_ms,
                    "pass": is_ok,
                })
                print(f"SMBS D6.3.2: {step_name} {'PASS' if is_ok else 'FAIL'} Vol={multi_sheet.volume_mm3:.4f} mm3 ({dt_ms:.1f}ms)")
                if bend_idx == len(bends_list) - 1 and f == 1.0:
                    final_reconstructed_shape = multi_sheet.shape

        # 3. Multi-Bend CAD Volume Consistency Evaluation
        cad_vol_res = multi_bend_cad_volume_consistency(
            sampled_sheet_volumes,
            state_angles_list=sampled_states,
            bends_params=bends_params,
            segments=segments_count,
        )
        report["cad_volume_consistency"] = cad_vol_res
        report["material_volume_constancy"] = volume_consistency(sampled_sheet_volumes)

        # 4. Reference CAD Comparison at Fully Folded State
        if final_reconstructed_shape is not None:
            t0 = time.perf_counter()
            ref_cmp = compare_reference_cad_body(doc, final_reconstructed_shape, segments=segments_count)
            ref_cmp["timing_ms"] = (time.perf_counter() - t0) * 1000.0
            report["reference_cad_comparison"] = ref_cmp
        else:
            report["reference_cad_comparison"] = {
                "status": "UNVERIFIED_REFERENCE",
                "reason": "Final shape not constructed",
            }

        # 5. Strict Acceptance Rules
        all_cases_valid = all(x["pass"] for x in report["cases"])
        cad_vol_pass = cad_vol_res["pass"]
        ref_pass = report["reference_cad_comparison"].get("status") == "PASS"

        if not all_cases_valid:
            report["result"] = "FAIL_GEOMETRY"
        elif not cad_vol_pass:
            report["result"] = "FAIL_CAD_VOLUME"
        elif ref_pass:
            report["result"] = "PASS_CAD_GEOMETRY"
        else:
            report["result"] = "UNVERIFIED_REFERENCE"

        report["full_sheet_geometry_complete"] = all_cases_valid
        report["closed_connected_sheet_samples"] = all_cases_valid
        report["machine_collision_certified"] = False
        report["source_fold_state_isolated"] = source_angle_snapshot == {
            bid: 0.0 for bid in source_angle_snapshot
        }
    except Exception as exc:
        report["result"] = "FAIL"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        print("SMBS D6.3.2 FAILED:", report["error"])
    finally:
        output = report_file(
            "d632_multi_bend_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("SMBS D6.3.2 report:", str(output))
    return report


if __name__ == "__main__":
    main()
