"""Launch the experimental 2D-only sequence search from FreeCAD Python Console.

No native 3D collision oracle is called. Requires a SheetMetal Unfold object
already present in the document (no implicit 3D unfold creation).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.kinematics2d.sequence_search_2d import search_bend_sequences_2d
from smbs_core.kinematics2d.tooling_25d import ToolKind, ToolLibrary25D, ToolingSetup2D

DEFAULT_PUNCH = "PUNCH_AP_1017_88°_H104,65_R0,8"
DEFAULT_DIE = "T-DIE_AT_3015_88°_V12_H80"


def run_planar_search(
    doc: Any,
    *,
    punch_id: str = DEFAULT_PUNCH,
    die_id: str = DEFAULT_DIE,
    max_solutions: int = 10,
    max_search_time_s: float = 60.0,
    max_nodes: int = 10000,
    max_evaluations: int = 50000,
    fraction_count: int = 17,
    stroke_fraction_count: int = 9,
    max_complete_candidates: int = 1000,
    output_path: str | None = None,
) -> dict:
    """Run section-only search without creating or mutating the input document."""
    if doc is None:
        raise ValueError("FreeCAD document is required")
    if not isinstance(fraction_count, int) or not 2 <= fraction_count <= 361:
        raise ValueError("fraction_count must be in [2,361]")
    if not isinstance(stroke_fraction_count, int) or not 2 <= stroke_fraction_count <= 361:
        raise ValueError("stroke_fraction_count must be in [2,361]")
    unfold = next((o for o in doc.Objects if o.Name.endswith("_Unfold")), None)
    if unfold is None:
        raise ValueError("SheetMetal Unfold object not found; create Unfold first")
    # Direct data-only path: do not create a Qt preview widget or trigger
    # drawing, 3D animation, press-cycle controllers, or GUI callbacks.
    session = build_part_session(doc, [unfold])
    library = ToolLibrary25D()
    tooling = ToolingSetup2D()
    punch = library.load_tool(punch_id, ToolKind.PUNCH)
    die = library.load_tool(die_id, ToolKind.DIE)
    tooling.select_punch(punch)
    tooling.select_die(die)
    fractions = tuple(i / (fraction_count - 1) for i in range(fraction_count))
    strokes = tuple(i / (stroke_fraction_count - 1) for i in range(stroke_fraction_count))
    report = search_bend_sequences_2d(
        session, tooling,
        max_solutions=max_solutions,
        max_complete_candidates=max_complete_candidates,
        max_search_time_s=max_search_time_s,
        max_nodes=max_nodes,
        max_evaluations=max_evaluations,
        fractions=fractions,
        stroke_fractions=strokes,
        match_active_bend_length=True,
    )
    payload = {
        "model": doc.Label,
        "punch": punch_id,
        "die": die_id,
        "virtual_tool_length": True,
        "fraction_count": fraction_count,
        "stroke_fraction_count": stroke_fraction_count,
        "report": report.to_dict(),
    }
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    return payload
