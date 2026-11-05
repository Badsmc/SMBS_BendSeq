"""D6.3 native Part single-bend smoke test on selected SheetMetal unfold.

In FreeCAD 1.1.3, open a SheetMetal document and select its Unfold object
(the same input used by the 2.5D GUI); run this script in Python console:
    import runpy, smbs_core
    from pathlib import Path
    _ = runpy.run_path(str(Path(smbs_core.__file__).resolve().parent.parent
                       / "tools" / "run_d63_single_bend.py"),
                       run_name="__main__")

The script makes no FreeCAD document objects. It tests the active (first)
bend's solid for five angles including endpoints; writes report to addon/reports.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.native_bend_solid_3d import build_bend_zone_solid


def main():
    report = {
        "schema": "smbs-d63-single-bend-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "ONE_REAL_SHEETMETAL_BEND_ZONE_NOT_COMPLETE_SHEET_OR_COLLISION_CERTIFICATION",
        "cases": [],
        "result": "NOT_RUN",
    }
    try:
        import FreeCAD as App
        import FreeCADGui as Gui
        from freecad_smbs.commands.pressbrake_2d import build_part_session

        doc=App.ActiveDocument
        selection=Gui.Selection.getSelection()
        if doc is None or not selection:
            raise ValueError(
                "Open real FCStd model, select its Body_Unfold object, and rerun"
            )
        source_angle_snapshot = {}
        session=build_part_session(doc,selection)
        state=session.fold_state
        source_angle_snapshot.update(state.angles)
        bid=session.active_bend_id
        z=state.bends[bid].bend_zone
        if z is None:
            raise ValueError("Selected bend has no SheetMetal BendZone")

        report.update({
            "freecad": ".".join(map(str,App.Version()[:3])),
            "part_label": session.part_label,
            "bend_id": bid,
            "target_angle_deg": state.bends[bid].angle_deg,
            "thickness_mm": z.thickness_mm,
            "inner_radius_mm": z.inner_radius_mm,
            "k_factor_ansi": z.k_factor_ansi,
            "bend_allowance_mm": z.bend_allowance_mm,
            "sampled_fractions": (0,0.25,0.5,0.75,1),
        })
        for fraction in (0.0,0.25,0.5,0.75,1.0):
            # Isolated in-memory FoldState, not GUI playback state.
            state.set_fraction(bid,fraction)
            result=build_bend_zone_solid(state,bid)
            expected_flat=(z.bend_allowance_mm*z.thickness_mm*result.bend_line_length_mm)
            flat_valid=(fraction!=0.0 or abs(result.volume_mm3-expected_flat)<max(1e-3,expected_flat*1e-5))
            row={
                "fraction": fraction,
                "angle_deg": state.angles[bid],
                "valid": bool(result.shape.isValid()),
                "closed": bool(result.shape.isClosed()),
                "solid_count": len(result.shape.Solids),
                "volume_mm3": result.volume_mm3,
                "expected_flat_volume_mm3": expected_flat if fraction==0 else None,
                "flat_volume_valid": flat_valid,
                "bend_line_length_mm": result.bend_line_length_mm,
                "section_area_mm2": result.section_area_mm2,
                "entire_sheet": False,
                "machine_collision_certified": False,
            }
            row["pass"] = (
                row["valid"] and row["closed"] and row["solid_count"]==1
                and row["volume_mm3"]>0 and flat_valid
            )
            report["cases"].append(row)
            print("SMBS D6.3:",bid, fraction, "PASS" if row["pass"] else "FAIL",
                  "Volume:",round(result.volume_mm3,6),"mm3")
        report["result"]="PASS_ZONE_ONLY" if all(x["pass"] for x in report["cases"]) else "FAIL"
        # Ensure the original document shape/GUI was never modified.
        report["source_fold_state_isolated"] = source_angle_snapshot == {
            bid:0.0 for bid in source_angle_snapshot
        }
    except Exception as exc:
        report["result"]="FAIL"
        report["error"]=str(exc)
        report["traceback"]=traceback.format_exc()
        print("SMBS D6.3 FAILED:",report["error"])
    finally:
        output=report_file(
            "d63_single_bend_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".json"
        )
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print("SMBS D6.3 report:",str(output))
    return report


if __name__=="__main__":
    main()
