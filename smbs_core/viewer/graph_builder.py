"""
graph_builder.py - Build BendGraph + PanelGraph for viewer (Stage 4, S.12).

Source of truth: 04_canonical_part.json (via RunArtifacts).
Scope: pure Python, no OCC, no FreeCAD, no solver/verifier/validator imports.

Honest marking (C2): missing mandatory fields raise; no silent defaults.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict

from smbs_core.Core.bend_graph import BendGraph, BendRecord, IncompleteBendError
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.viewer.run_loader import RunArtifacts


class GraphBuilderError(Exception):
    """Raised when 04_canonical_part payload is missing mandatory fields or has invalid values."""


@dataclass(frozen=True)
class ViewerGraphs:
    bend_graph: BendGraph
    panel_graph: PanelGraph
    direction_by_bend: dict[str, str]   # {bend_id: "UP"|"DOWN"} — UI label only


def build_graphs(artifacts: RunArtifacts) -> ViewerGraphs:
    """
    Build BendGraph + PanelGraph from 04_canonical_part.

    Raises GraphBuilderError on:
      - payload.thickness_mm.value missing
      - payload.panels / payload.bends missing
      - for any panel: normal missing
      - for any bend: parent_panel_id, child_panel_id, axis_point, axis_direction,
        angle_deg.value, radius_mm.value, length_mm.value missing
      - direction.value not in {"UP", "DOWN"}
      - signed_angle out of [-180, 180] (IncompleteBendError wrapped)
      - any candidate_sequence[i].bend_id not present in bend_graph
    Empty candidate_sequence is VALID (search failure case).
    """
    cp = artifacts.canonical_part
    sr = artifacts.search_result

    # 1. Validate top-level payload structure
    try:
        payload = cp["payload"]
    except (KeyError, TypeError) as err:
        raise GraphBuilderError(f"Missing 'payload' in canonical_part: {err}") from err

    try:
        thickness_val = payload["thickness_mm"]["value"]
    except (KeyError, TypeError) as err:
        raise GraphBuilderError(f"Missing 'thickness_mm.value' in canonical_part payload: {err}") from err

    try:
        panels_dict: dict = payload["panels"]
    except (KeyError, TypeError) as err:
        raise GraphBuilderError(f"Missing 'panels' in canonical_part payload: {err}") from err

    try:
        bends_dict: dict = payload["bends"]
    except (KeyError, TypeError) as err:
        raise GraphBuilderError(f"Missing 'bends' in canonical_part payload: {err}") from err

    # 2. Build PanelGraph
    panel_graph = PanelGraph()
    for pid, pdata in panels_dict.items():
        try:
            normal_raw = pdata["normal"]
        except (KeyError, TypeError) as err:
            raise GraphBuilderError(f"Panel '{pid}' missing mandatory 'normal': {err}") from err

        if not isinstance(normal_raw, (list, tuple)) or len(normal_raw) != 3:
            raise GraphBuilderError(f"Panel '{pid}' normal must be a 3-element vector, got: {normal_raw}")

        panel_graph.add_panel(Panel(pid, normal=tuple(normal_raw)))

    for bid, bdata in bends_dict.items():
        try:
            parent_p = bdata["parent_panel_id"]
            child_p = bdata["child_panel_id"]
        except (KeyError, TypeError) as err:
            raise GraphBuilderError(f"Bend '{bid}' missing parent/child panel ids: {err}") from err

        panel_graph.add_bend_connection(bid, parent_p, child_p)

    # 3. Build BendGraph and direction map
    bend_graph = BendGraph()
    direction_by_bend: Dict[str, str] = {}

    for bid, bdata in bends_dict.items():
        for field in ("parent_panel_id", "child_panel_id", "axis_point", "axis_direction"):
            if field not in bdata:
                raise GraphBuilderError(f"Bend '{bid}' missing mandatory field '{field}'")

        for obj_field in ("angle_deg", "radius_mm", "length_mm", "direction"):
            if obj_field not in bdata:
                raise GraphBuilderError(f"Bend '{bid}' missing mandatory field '{obj_field}'")
            if not isinstance(bdata[obj_field], dict) or "value" not in bdata[obj_field]:
                raise GraphBuilderError(f"Bend '{bid}' missing mandatory field '{obj_field}.value'")

        parent_p = bdata["parent_panel_id"]
        child_p = bdata["child_panel_id"]
        axis_pt = bdata["axis_point"]
        axis_dir = bdata["axis_direction"]
        signed_angle = bdata["angle_deg"]["value"]
        radius = bdata["radius_mm"]["value"]
        length = bdata["length_mm"]["value"]
        direction_val = bdata["direction"]["value"]

        if direction_val not in ("UP", "DOWN"):
            raise GraphBuilderError(f"Bend '{bid}' direction.value must be 'UP' or 'DOWN', got: '{direction_val}'")

        direction_by_bend[bid] = direction_val

        try:
            rec = BendRecord(
                bend_id=bid,
                feature_id=bid,
                parent_panel_id=parent_p,
                child_panel_id=child_p,
                axis_origin=tuple(axis_pt),
                axis_direction=tuple(axis_dir),
                signed_angle=signed_angle,
                radius=radius,
                thickness=thickness_val,
                length=length
            )
        except IncompleteBendError as err:
            raise GraphBuilderError(f"BendRecord validation failed for '{bid}': {err}") from err
        except Exception as err:
            raise GraphBuilderError(f"Failed to create BendRecord for '{bid}': {err}") from err

        bend_graph.add_bend(rec)

    # 4. Validate candidate_sequence consistency with bend_graph
    try:
        seq = sr["payload"]["candidate_sequence"]
    except (KeyError, TypeError) as err:
        raise GraphBuilderError(f"Missing 'candidate_sequence' in search_result payload: {err}") from err

    all_bend_ids = {b.id for b in bend_graph.all_bends()}
    for step_info in seq:
        step_bid = step_info.get("bend_id")
        if step_bid not in all_bend_ids:
            raise GraphBuilderError(
                f"candidate_sequence step refers to unknown bend_id '{step_bid}' (not in canonical_part bends)"
            )

    return ViewerGraphs(
        bend_graph=bend_graph,
        panel_graph=panel_graph,
        direction_by_bend=direction_by_bend
    )
