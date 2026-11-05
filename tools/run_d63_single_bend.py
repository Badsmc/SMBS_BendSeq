"""D6.3 native Part single-bend smoke test on selected SheetMetal unfold.

In FreeCAD 1.1.3, open a SheetMetal document and select its Unfold object
(the same input used by the 2.5D GUI); run this script in Python console:
    import runpy, smbs_core
    from pathlib import Path
    _ = runpy.run_path(str(Path(smbs_core.__file__).resolve().parent.parent
                       / "tools" / "run_d63_single_bend.py"),
                       run_name="__main__")

The script makes no FreeCAD document objects. It tests the active (first)
bend's solid for seven angles including endpoints; writes report to addon/reports.
"""
from __future__ import annotations

import json
import math
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.native_volume_gate import (
    volume_consistency,
    ideal_constant_thickness_bend_drift_mm3,
    polygonal_bend_discretization_error_mm3,
    predict_cad_consistent_volume_mm3,
    cad_volume_consistency,
)
from smbs_core.kinematics2d.native_bend_solid_3d import (
    build_bend_zone_solid,
    build_single_bend_sheet_solid,
    NativeBendSolidError,
)


def resolve_test_selection(doc, selected):
    """Select a unique SheetMetal unfold when FreeCAD GUI selection is empty.

    Never guesses among multiple unfolds. Explicit selection remains owned
    by the existing build_part_session/resolve_selected_unfold logic.
    """
    if doc is None:
        raise ValueError(
            "No active FreeCAD document. Open an FCStd SheetMetal model."
        )
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

    # Explicit frame alignment detection
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

    # Match criteria: common volume covers >= 99.9% of body volume,
    # cut residuals are accounted for by discretization
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
        "schema": "smbs-d631-single-bend-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "ONE_REAL_SHEETMETAL_BEND_ZONE_AND_OPTIONAL_TWO_PANEL_SHEET_NOT_COLLISION_CERTIFICATION",
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
        bid = session.active_bend_id
        z = state.bends[bid].bend_zone
        if z is None:
            raise ValueError("Selected bend has no SheetMetal BendZone")

        sampled_fractions = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)
        report.update({
            "freecad": ".".join(map(str, App.Version()[:3])),
            "part_label": session.part_label,
            "bend_id": bid,
            "target_angle_deg": state.bends[bid].angle_deg,
            "thickness_mm": z.thickness_mm,
            "inner_radius_mm": z.inner_radius_mm,
            "outer_radius_mm": z.outer_radius_mm,
            "k_factor_ansi": z.k_factor_ansi,
            "bend_allowance_mm": z.bend_allowance_mm,
            "sampled_fractions": sampled_fractions,
        })

        segments_count = 64
        final_reconstructed_shape = None

        for fraction in sampled_fractions:
            state.set_fraction(bid, fraction)
            result = build_bend_zone_solid(state, bid, segments=segments_count)
            expected_flat = (z.bend_allowance_mm * z.thickness_mm * result.bend_line_length_mm)
            flat_valid = (fraction != 0.0 or abs(result.volume_mm3 - expected_flat) < max(1e-3, expected_flat * 1e-5))
            row = {
                "fraction": fraction,
                "angle_deg": state.angles[bid],
                "valid": bool(result.shape.isValid()),
                "closed": bool(result.shape.isClosed()),
                "solid_count": len(result.shape.Solids),
                "volume_mm3": result.volume_mm3,
                "expected_flat_volume_mm3": expected_flat if fraction == 0 else None,
                "flat_volume_valid": flat_valid,
                "bend_line_length_mm": result.bend_line_length_mm,
                "section_area_mm2": result.section_area_mm2,
                "entire_sheet": False,
                "machine_collision_certified": False,
            }
            if len(state.ordered_bends) == 1:
                try:
                    sheet = build_single_bend_sheet_solid(
                        state, session.panel_build, bid, segments=segments_count
                    )
                    row["full_single_bend_sheet"] = {
                        "status": "PASS",
                        "volume_mm3": sheet.volume_mm3,
                        "solids": len(sheet.shape.Solids),
                        "is_valid": bool(sheet.shape.isValid()),
                        "physical_certified": False,
                    }
                    if fraction == 1.0:
                        final_reconstructed_shape = sheet.shape
                except Exception as exc:
                    row["full_single_bend_sheet"] = {
                        "status": "FAIL",
                        "reason": str(exc),
                    }
            else:
                row["full_single_bend_sheet"] = {
                    "status": "NOT_RUN",
                    "reason": "Requires exactly one bend and two panels",
                }
            row["pass"] = (
                row["valid"] and row["closed"] and row["solid_count"] == 1
                and row["volume_mm3"] > 0 and flat_valid
            )
            report["cases"].append(row)
            print("SMBS D6.3.1:", bid, fraction, "PASS" if row["pass"] else "FAIL",
                  "Volume:", round(result.volume_mm3, 6), "mm3")

        # 1. Zone Volume Evaluation
        zone_volumes = [r["volume_mm3"] for r in report["cases"]]
        zone_angles = [r["angle_deg"] for r in report["cases"]]
        L_bend = report["cases"][0]["bend_line_length_mm"]

        report["zone_cad_volume_consistency"] = cad_volume_consistency(
            zone_volumes,
            angles_deg=zone_angles,
            thickness_mm=z.thickness_mm,
            k_factor=z.k_factor_ansi,
            bend_length_mm=L_bend,
            inner_radius_mm=z.inner_radius_mm,
            segments=segments_count,
        )
        report["zone_material_volume_constancy"] = volume_consistency(zone_volumes)

        # 2. Full Sheet Volume Evaluation
        full_sheet_rows = [r["full_single_bend_sheet"] for r in report["cases"]]
        complete_sheets = all(r["status"] == "PASS" for r in full_sheet_rows)

        if complete_sheets:
            sheet_volumes = [r["volume_mm3"] for r in full_sheet_rows]
            report["full_sheet_cad_volume_consistency"] = cad_volume_consistency(
                sheet_volumes,
                angles_deg=zone_angles,
                thickness_mm=z.thickness_mm,
                k_factor=z.k_factor_ansi,
                bend_length_mm=L_bend,
                inner_radius_mm=z.inner_radius_mm,
                segments=segments_count,
            )
            report["full_sheet_material_volume_constancy"] = volume_consistency(sheet_volumes)
        else:
            report["full_sheet_cad_volume_consistency"] = {
                "pass": False,
                "status": "NOT_RUN",
                "reason": "full_sheet_not_built_for_all_samples",
            }
            report["full_sheet_material_volume_constancy"] = {
                "pass": False,
                "status": "NOT_RUN",
                "reason": "full_sheet_not_built_for_all_samples",
            }

        # 3. Reference CAD Comparison at 100% Fold State
        if complete_sheets and final_reconstructed_shape is not None:
            ref_cmp = compare_reference_cad_body(doc, final_reconstructed_shape, segments=segments_count)
            report["reference_cad_comparison"] = ref_cmp
        else:
            report["reference_cad_comparison"] = {
                "status": "UNVERIFIED_REFERENCE",
                "reason": "Full sheet solid not available for reference CAD comparison",
            }

        # 4. Strict Unambiguous Acceptance Status Rules
        all_cases_valid = all(x["pass"] for x in report["cases"])
        cad_zone_pass = report["zone_cad_volume_consistency"]["pass"]
        cad_sheet_pass = complete_sheets and report["full_sheet_cad_volume_consistency"].get("pass", False)
        ref_pass = report["reference_cad_comparison"].get("status") == "PASS"

        if not all_cases_valid:
            report["result"] = "FAIL_GEOMETRY"
        elif not cad_zone_pass or (complete_sheets and not cad_sheet_pass):
            report["result"] = "FAIL_CAD_VOLUME"
        elif complete_sheets and cad_sheet_pass and ref_pass:
            report["result"] = "PASS_CAD_GEOMETRY"
        elif complete_sheets and cad_sheet_pass:
            report["result"] = "UNVERIFIED_REFERENCE"
        else:
            report["result"] = "PASS_ZONE_ONLY"

        report["cad_volume_consistency"] = {
            "status": "PASS" if (cad_zone_pass and (not complete_sheets or cad_sheet_pass)) else "FAIL",
            "reference_model": "SHEETMETAL_CONSTANT_THICKNESS_CONCENTRIC_BEND",
            "maximum_absolute_residual_mm3": report["full_sheet_cad_volume_consistency"].get(
                "maximum_absolute_residual_mm3",
                report["zone_cad_volume_consistency"]["maximum_absolute_residual_mm3"]
            ),
            "maximum_relative_residual_percent": report["full_sheet_cad_volume_consistency"].get(
                "maximum_relative_residual_percent",
                report["zone_cad_volume_consistency"]["maximum_relative_residual_percent"]
            ),
        }
        report["material_volume_constancy"] = {
            "status": "NOT_CONSTANT" if not report["zone_material_volume_constancy"]["pass"] else "CONSTANT",
            "interpretation": "CAD_GEOMETRY_ONLY_K_FACTOR_DEFORMATION",
        }

        report["full_sheet_geometry_complete"] = complete_sheets
        report["closed_connected_sheet_samples"] = complete_sheets
        report["machine_collision_certified"] = False
        report["source_fold_state_isolated"] = source_angle_snapshot == {
            bid: 0.0 for bid in source_angle_snapshot
        }
    except Exception as exc:
        report["result"] = "FAIL"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        print("SMBS D6.3.1 FAILED:", report["error"])
    finally:
        output = report_file(
            "d631_single_bend_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("SMBS D6.3.1 report:", str(output))
    return report


if __name__ == "__main__":
    main()
