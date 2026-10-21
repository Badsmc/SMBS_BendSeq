"""
astar_backward.py - A* Backward Search Planner with State Cache Integration
"""

import time
import heapq
import logging
from typing import Dict, Any, List, Optional, Set, Tuple

from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.bend_graph import BendGraph
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.state_cache import StateCache
from smbs_core.Physics.validator import PhysicalValidator

logger = logging.getLogger(__name__)


class PriorityQueueNode:
    def __init__(
        self,
        f_score: float,
        state: Optional[FoldState],
        path: Optional[List[Dict[str, Any]]],
        counter: int = 0,
        pending: Optional[Dict[str, Any]] = None
    ):
        self.f_score = f_score
        self.state = state
        self.path = path
        self.counter = counter
        self.pending = pending

    def __lt__(self, other: "PriorityQueueNode") -> bool:
        if abs(self.f_score - other.f_score) > 1e-6:
            return self.f_score < other.f_score
        return self.counter < other.counter


class AStarBackwardPlanner:
    def __init__(
        self,
        bend_graph: BendGraph,
        validator: PhysicalValidator,
        panel_graph: Optional[PanelGraph] = None,
        state_cache: Optional[StateCache] = None
    ):
        self.bend_graph = bend_graph
        self.validator = validator
        self.panel_graph = panel_graph
        self.cache = state_cache or StateCache()
        self.nodes_explored = 0

    def compute_heuristic(self, state: FoldState, bend_id: Optional[str] = None) -> float:
        """
        Admissible heuristic guiding backward unfold search.
        In backward planning, unfolding inner bends (larger subtree) is penalized so that
        outer bends unfold first, leading naturally to center-first forward sequences.
        """
        h = float(len(state.remaining_bends))
        if self.panel_graph and bend_id:
            moving_count = len(self.panel_graph.moving_subtree(bend_id))
            h += 0.1 * moving_count
        return h

    def solve(
        self,
        initial_state: FoldState,
        max_iterations: int = 5000,
        timeout: Optional[float] = None,
        unknown_policy: str = "PRODUCTION"
    ) -> Dict[str, Any]:
        start_time = time.time()
        self.nodes_explored = 0
        rejections = []
        counter = 0

        dead_states: Set[str] = set()
        open_set: List[PriorityQueueNode] = []
        start_fp = initial_state.fingerprint()

        # Consult state cache
        cached_entry = self.cache.get(start_fp)
        if not cached_entry:
            self.cache.put(start_fp, {"visited": True, "state": initial_state})

        counter += 1
        heapq.heappush(open_set, PriorityQueueNode(0.0, initial_state, [], counter))

        g_scores: Dict[str, float] = {start_fp: 0.0}

        while open_set and self.nodes_explored < max_iterations:
            if timeout is not None and (time.time() - start_time) >= timeout:
                return {
                    "success": False,
                    "termination_reason": "TIME_EXHAUSTED",
                    "error": f"Search timed out after {timeout:.4f}s.",
                    "nodes_explored": self.nodes_explored,
                    "planning_time": time.time() - start_time,
                    "rejections": rejections
                }

            current_node = heapq.heappop(open_set)

            if current_node.pending is not None:
                # --- Lazy A*: validation deferred from heappush to heappop ---
                p = current_node.pending
                pred_fp = p["pred_fp"]

                if pred_fp in dead_states:
                    continue

                bend_rec_pop = self.bend_graph.bends[p["bend_id"]]

                # Re-check cache: another pop may have validated this fingerprint
                cached_node = self.cache.get(pred_fp)
                if cached_node and "val_res" in cached_node and "state" in cached_node:
                    val_res = cached_node["val_res"]
                    next_state = cached_node["state"]
                else:
                    kwargs = {}
                    if unknown_policy != "PRODUCTION":
                        kwargs["unknown_policy"] = unknown_policy
                    else:
                        try:
                            import inspect
                            if "unknown_policy" in inspect.signature(self.validator.validate_step).parameters:
                                kwargs["unknown_policy"] = unknown_policy
                        except Exception:
                            pass
                    val_res = self.validator.validate_step(
                        current_state=p["parent_state"],
                        bend_record=bend_rec_pop,
                        panel_graph=self.panel_graph,
                        **kwargs
                    )
                    next_state = val_res.next_state

                coll_raw = getattr(val_res, "collision_status", "VALID")
                c_stat = coll_raw.value if hasattr(coll_raw, "value") else str(coll_raw)
                tool_raw = getattr(val_res, "tooling_status", "SIMPLIFIED")
                t_stat = tool_raw.value if hasattr(tool_raw, "value") else str(tool_raw)
                align_raw = getattr(val_res, "alignment_status", "CANNOT_VERIFY")
                a_stat = align_raw.value if hasattr(align_raw, "value") else str(align_raw)

                if not val_res.valid or next_state is None:
                    dead_states.add(pred_fp)
                    rejections.append({
                        "bend_id": p["bend_id"],
                        "stage": val_res.stage,
                        "reason": val_res.reason,
                        "collision_status": c_stat,
                        "tooling_status": t_stat,
                        "alignment_status": a_stat
                    })
                    continue

                self.cache.put(pred_fp, {"visited": True, "state": next_state, "val_res": val_res})

                step_info = {
                    "step_number": p["step_number"],
                    "bend_id": p["bend_id"],
                    "angle": bend_rec_pop.signed_angle,
                    "radius": bend_rec_pop.radius,
                    "length": bend_rec_pop.length,
                    "axis": bend_rec_pop.axis_direction,
                    "origin": bend_rec_pop.axis_origin,
                    "moving_panels": list(self.panel_graph.moving_subtree(p["bend_id"])) if self.panel_graph else [],
                    "collision_status": c_stat,
                    "tooling_status": t_stat,
                    "alignment_status": a_stat,
                    "has_unknown": getattr(val_res, "has_unknown", False)
                }

                current_state = next_state
                current_path = p["parent_path"] + [step_info]
                current_fp = pred_fp
                current_g = p["parent_g"] + 1.0
            else:
                current_state = current_node.state
                current_path = current_node.path
                current_fp = current_state.fingerprint()

                if current_fp in dead_states:
                    continue

                current_g = g_scores.get(current_fp, float('inf'))

            self.nodes_explored += 1

            if current_state.is_goal():
                forward_sequence = list(reversed(current_path))
                for idx, step in enumerate(forward_sequence):
                    step["step_number"] = idx + 1
                seq_has_unknown = any(s.get("has_unknown", False) or s.get("collision_status") == "UNKNOWN" for s in forward_sequence)
                return {
                    "success": True,
                    "sequence": forward_sequence,
                    "sequence_has_unknown": seq_has_unknown,
                    "nodes_explored": self.nodes_explored,
                    "planning_time": time.time() - start_time,
                    "rejections": rejections
                }

            def bend_sort_key(b_id: str):
                moving_sz = len(self.panel_graph.moving_subtree(b_id)) if self.panel_graph else 0
                return (moving_sz, b_id)

            candidates = sorted(list(current_state.remaining_bends), key=bend_sort_key)

            for bend_id in candidates:
                bend_rec = self.bend_graph.bends[bend_id]

                moving_panels_for_pred = self.panel_graph.moving_subtree(bend_id) if self.panel_graph else None

                # 1. Predictive Fingerprint & Cache check for GEOMETRY REUSE
                pred_fp = current_state.predict_successor_fingerprint(
                    bend_id,
                    moving_panels=moving_panels_for_pred,
                    panel_graph=self.panel_graph
                )
                if pred_fp in dead_states:
                    continue

                cached_node = self.cache.get(pred_fp)
                if cached_node and "val_res" in cached_node and "state" in cached_node:
                    # GEOMETRY & VALIDATION REUSE — push fully-formed valid node (same as eager)
                    val_res = cached_node["val_res"]
                    next_state = cached_node["state"]

                    coll_raw = getattr(val_res, "collision_status", "VALID")
                    c_stat = coll_raw.value if hasattr(coll_raw, "value") else str(coll_raw)
                    tool_raw = getattr(val_res, "tooling_status", "SIMPLIFIED")
                    t_stat = tool_raw.value if hasattr(tool_raw, "value") else str(tool_raw)
                    align_raw = getattr(val_res, "alignment_status", "CANNOT_VERIFY")
                    a_stat = align_raw.value if hasattr(align_raw, "value") else str(align_raw)

                    next_fp = next_state.fingerprint()
                    tentative_g = current_g + 1.0

                    if tentative_g < g_scores.get(next_fp, float('inf')):
                        g_scores[next_fp] = tentative_g

                        try:
                            h_score = self.compute_heuristic(next_state, bend_id)
                        except TypeError:
                            h_score = self.compute_heuristic(next_state)

                        f_score = tentative_g + h_score

                        step_info = {
                            "step_number": len(current_path) + 1,
                            "bend_id": bend_id,
                            "angle": bend_rec.signed_angle,
                            "radius": bend_rec.radius,
                            "length": bend_rec.length,
                            "axis": bend_rec.axis_direction,
                            "origin": bend_rec.axis_origin,
                            "moving_panels": list(moving_panels_for_pred) if moving_panels_for_pred is not None else [],
                            "collision_status": c_stat,
                            "tooling_status": t_stat,
                            "alignment_status": a_stat,
                            "has_unknown": getattr(val_res, "has_unknown", False)
                        }

                        counter += 1
                        heapq.heappush(open_set, PriorityQueueNode(f_score, next_state, current_path + [step_info], counter))
                else:
                    # Cache miss — push pending; validate deferred to heappop (Lazy A*)
                    tentative_g = current_g + 1.0

                    if tentative_g < g_scores.get(pred_fp, float('inf')):
                        g_scores[pred_fp] = tentative_g

                        h_pred = float(len(current_state.remaining_bends) - 1)
                        if self.panel_graph and moving_panels_for_pred is not None:
                            h_pred += 0.1 * len(moving_panels_for_pred)

                        f_score = tentative_g + h_pred

                        counter += 1
                        heapq.heappush(open_set, PriorityQueueNode(
                            f_score, None, None, counter,
                            pending={
                                "parent_state": current_state,
                                "parent_path": current_path,
                                "parent_g": current_g,
                                "bend_id": bend_id,
                                "pred_fp": pred_fp,
                                "step_number": len(current_path) + 1
                            }
                        ))

        if self.nodes_explored >= max_iterations:
            return {
                "success": False,
                "termination_reason": "NODE_LIMIT_EXHAUSTED",
                "error": f"Max iterations budget reached ({max_iterations}).",
                "nodes_explored": self.nodes_explored,
                "planning_time": time.time() - start_time,
                "rejections": rejections
            }

        return {
            "success": False,
            "termination_reason": "NO_SOLUTION",
            "error": "Search space exhausted; no valid sequence path found.",
            "nodes_explored": self.nodes_explored,
            "planning_time": time.time() - start_time,
            "rejections": rejections
        }
