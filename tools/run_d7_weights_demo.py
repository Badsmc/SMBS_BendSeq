"""Manual FreeCAD console smoke test for D7 operational weights (no UI install).

Run inside FreeCAD 1.1:
    import runpy
    from pathlib import Path
    import smbs_core
    runner = Path(smbs_core.__file__).resolve().parent.parent / "tools" / "run_d7_weights_demo.py"
    _ = runpy.run_path(str(runner), run_name="__main__")

Synthetic *machine setups*, not proof of actual machine positioning or
collision-free bends. JSON report saved in addon/reports by shared helper.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.report_paths import report_file
from smbs_core.kinematics2d.operation_cost import (
    BendSetup, OperationWeights, score_sequence,
)
from smbs_core.kinematics2d.panel_graph import GraphEdge, PanelGraph
from smbs_core.kinematics2d.panel_kinematics import FoldState
from smbs_core.kinematics2d.sequence_search import (
    Feasibility, REQUIRED_SCOPES, StepEvidence,
)
from smbs_core.kinematics2d.weighted_sequence_search import plan_weighted_forward


def main():
    # Explicitly synthetic orientations and sheet flip states. Never infer
    # these from SheetMetal bend angle sign.
    setups = {
        "B01": BendSetup("B01", 0.0, False, 100.0),
        "B02": BendSetup("B02", 180.0, True, 120.0),
        "B03": BendSetup("B03", 0.0, False, 100.0),
    }
    weights = OperationWeights()
    graph = PanelGraph(("P0", "P1", "P2", "P3"), (
        GraphEdge("B01", "P0", "P1", 90.0,
                  ((0.,0.,0.), (100.,0.,0.))),
        GraphEdge("B02", "P1", "P2", -90.0,
                  ((0.,20.,0.), (120.,20.,0.))),
        GraphEdge("B03", "P2", "P3", 90.0,
                  ((0.,40.,0.), (100.,40.,0.))),
    ))
    state = FoldState(graph, "P0")
    orders = (
        ("B01", "B02", "B03"),
        ("B01", "B03", "B02"),
        ("B02", "B01", "B03"),
    )
    ranking = sorted(
        (score_sequence(order, setups, weights) for order in orders),
        key=lambda scored: (scored.total, scored.order),
    )
    def synthetic_oracle(*_args):
        # This is deliberately synthetic full-scopes data for testing
        # the optimization contract. It has no physical evidentiary value.
        return StepEvidence(
            Feasibility.CLEAR, "SYNTHETIC_FOR_WEIGHT_TEST_ONLY",
            REQUIRED_SCOPES,
        )
    winner = plan_weighted_forward(state, synthetic_oracle, setups, weights)
    checks = {
        "ranked_ascending": all(
            ranking[i].total <= ranking[i+1].total
            for i in range(len(ranking)-1)
        ),
        "optimizer_agrees_with_lowest": (
            winner.success and abs(winner.cost.total - ranking[0].total) < 1e-9
        ),
        "all_bends_present": (
            set(winner.sequence) == set(setups)
        ),
        "source_angles_unchanged": all(
            angle == 0 for angle in state.angles.values()
        ),
    }
    report = {
        "schema": "smbs-d7-weight-demo-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "SYNTHETIC_COST_ONLY_NOT_PHYSICALLY_CERTIFIED",
        "weights": {field: getattr(weights, field)
                    for field in weights.__dataclass_fields__},
        "tested_orders": [scored.to_dict() for scored in ranking],
        "optimizer": {
            "status": winner.status.value,
            "synthetic_oracle_only": True,
            "sequence": list(winner.sequence),
            "total_cost": winner.cost.total if winner.cost else None,
            "explored_states": winner.explored_states,
            "checked_steps": winner.checked_steps,
            "optimality_proven_for_synthetic_model": winner.optimality_proven,
        },
        "checks": checks,
        "acceptance": "PASS" if all(checks.values()) else "FAIL",
    }
    output = report_file(
        "d7_weights_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nSMBS D7 WEIGHTS TEST: " + report["acceptance"])
    for scored in ranking:
        print(" -> ".join(scored.order), "penalty:", round(scored.total, 3))
    print("Selected:", " -> ".join(winner.sequence))
    print("Report:", str(output))
    return report


if __name__ == "__main__":
    main()
