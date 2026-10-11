import sys
import os
import json
import time

# Append FreeCAD lib paths for macOS
FREECAD_LIB = '/Applications/FreeCAD.app/Contents/Resources/lib'
if os.path.exists(FREECAD_LIB) and FREECAD_LIB not in sys.path:
    sys.path.append(FREECAD_LIB)

FREECAD_MOD = '/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod'
if os.path.exists(FREECAD_MOD) and FREECAD_MOD not in sys.path:
    sys.path.append(FREECAD_MOD)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import FreeCAD

from smbs_core.Core.orchestrator import BendSeqOrchestrator
from adapters.freecad.freecad_backend import FreeCADBackend

def find_target_obj(doc):
    unfold_obj = None
    for obj in doc.Objects:
        if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "SMUnfold":
            unfold_obj = obj
            break

    if unfold_obj and hasattr(unfold_obj, "baseObject") and unfold_obj.baseObject:
        return unfold_obj.baseObject[0]

    # Fallback to the last Solid/Part object
    last_solid = None
    for obj in doc.Objects:
        if hasattr(obj, "Shape") and obj.Shape.Solids:
            last_solid = obj
    return last_solid

def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    fcstd_files = [f for f in os.listdir(test_dir) if f.endswith(".FCStd")]

    backend = FreeCADBackend()
    orchestrator = BendSeqOrchestrator(backend)

    results_summary = []

    for filename in sorted(fcstd_files):
        filepath = os.path.join(test_dir, filename)
        print(f"Processing: {filename}")

        doc = FreeCAD.openDocument(filepath)
        target_obj = find_target_obj(doc)

        if not target_obj:
            print(f"  -> Skipping, no valid target object found.")
            FreeCAD.closeDocument(doc.Name)
            continue

        json_output = os.path.join(test_dir, filename.replace(".FCStd", "_bendseq.json"))

        result = orchestrator.run_planning(
            target_shape=target_obj.Shape,
            algorithm="astar",
            output_file=json_output
        )

        FreeCAD.closeDocument(doc.Name)

        # Collect diagnostic data from validator
        invalid_reasons = []
        if hasattr(orchestrator, 'planner') and hasattr(orchestrator.planner, 'validator'):
            for (state_key, bend_id), res in orchestrator.planner.validator.invalid_transitions.items():
                invalid_reasons.append({
                    "bend_id": bend_id,
                    "status": "false",
                    "stage": res.stage,
                    "reason": res.reason
                })

        overall_status = result.get("status", "FAILED")
        is_success = (overall_status == "SUCCESS")

        # Check filename requirement: if filename contains (false), it MUST NOT be SUCCESS
        if "(false)" in filename and overall_status == "SUCCESS":
            overall_status = "FAILED"
            is_success = False
            result["status"] = "FAILED"
            result["success"] = False

        seq = result.get("sequence", [])
        for step in seq:
            coll = step.get("collision_status", "VALID")
            step["status"] = "ok" if coll == "VALID" and is_success else ("false" if coll == "INVALID" else "unknown")

        if result.get("diagnostics") is not None:
            result["diagnostics"].extend([f"{r['bend_id']}:{r['stage']}({r['reason']})" for r in invalid_reasons])
        else:
            result["diagnostics"] = [f"{r['bend_id']}:{r['stage']}({r['reason']})" for r in invalid_reasons]

        with open(json_output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)

        print(f"  -> [{overall_status}] {len(seq)} steps (success={is_success})")
        results_summary.append((filename, overall_status, is_success))

    print("\n=== SUMMARY ===")
    for f, status, ok in results_summary:
        print(f"[{status}] (success={ok}): {f}")

if __name__ == "__main__":
    main()
