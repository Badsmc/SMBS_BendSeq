"""
dfs_backward.py - Reference Backward DFS Solver with Physical Oracle (SMBS Phase 6 & Stage 1 Optimization)

Stage 1 Performance & Memory Requirements implemented:
1. Lightweight SearchState / CameFrom mapping (no heavy parent pointers).
2. Dead States set pruning.
3. Validation Cache: (state_fingerprint, bend_id, toolset) -> ValidationResult.
4. Pre-filter stage: early exit before triggering OCC collision detection.
5. Instant OCC Shape nullification / GC cleanup on backtrack.
6. Detailed search metrics tracking.
"""

import time
from typing import Dict, Any, List, Optional, Set, Tuple
from smbs_core.Core.fold_state import FoldState, SearchState
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Physics.validator import PhysicalValidator, ValidationResult
from .base_planner import BasePlanner


class DFSBackwardPlanner(BasePlanner):
    """
    Optimized Reference Backward Depth-First Search (DFS) Planner.
    """

    def __init__(
        self,
        bend_graph: BendGraph,
        validator: PhysicalValidator,
        panel_graph: Optional[PanelGraph] = None,
        state_cache: Optional[Any] = None
    ):
        super().__init__(validator)
        self.bend_graph = bend_graph
        self.panel_graph = panel_graph
        self.cache = state_cache
        self.rejection_records: List[Dict[str, Any]] = []
        
        # Local validation cache if global is not provided
        self.validation_cache: Dict[Tuple[str, str, str], ValidationResult] = {}
        
        # Metrics
        self.metrics = {
            "explored_count": 0,
            "cache_hits": 0,
            "prefilter_rejected": 0,
            "dead_states_hit": 0,
            "occ_evaluations": 0
        }

    def solve(
        self,
        initial_state: FoldState,
        max_depth: int = 500,
        timeout: Optional[float] = None,
        tooling_override: Optional[Dict[str, Any]] = None,
        max_iterations: int = 5000,
        unknown_policy: str = "PRODUCTION"
    ) -> Dict[str, Any]:
        """
        Execute optimized backward DFS search with backtracking, validation cache, and dead-state pruning.
        """
        start_time = time.time()
        self.rejection_records.clear()
        self.validation_cache.clear()
        self.metrics = {
            "explored_count": 0,
            "cache_hits": 0,
            "prefilter_rejected": 0,
            "dead_states_hit": 0,
            "occ_evaluations": 0
        }
        
        dead_states: Set[str] = set()
        
        # Determine toolset hash taking override into account
        tool_state = tooling_override if tooling_override else initial_state.tooling_state
        toolset_hash = f"{tool_state.get('punch_id')}_{tool_state.get('die_id')}"

        self.timed_out = False
        self.budget_exhausted = False

        def _dfs(current: FoldState, depth: int, current_path: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
            self.metrics["explored_count"] += 1
            
            if timeout is not None and (time.time() - start_time) > timeout:
                self.timed_out = True
                return None

            if self.metrics["explored_count"] >= max_iterations:
                self.budget_exhausted = True
                return None
            
            fingerprint = current.fingerprint()
            if fingerprint in dead_states:
                self.metrics["dead_states_hit"] += 1
                return None

            if current.is_goal():
                return list(current_path)

            if depth >= max_depth:
                self.budget_exhausted = True
                return None

            def score_candidate(b_id: str) -> float:
                """
                Stage 10: Compute comprehensive priority score for the candidate.
                Lower score = tried first.
                Factors:
                1. Moving subtree size (outermost bends first -> smaller subtree)
                2. Orientation (prefer continuous bends without flipping the sheet)
                3. Tooling / Physical constraints (longer bends unfolded first)
                """
                score = 0.0
                bend_rec = self.bend_graph.get_bend(b_id)
                if not bend_rec:
                    return 0.0
                    
                # 1. Size of moving subtree
                if self.panel_graph:
                    score += len(self.panel_graph.moving_subtree(b_id)) * 10.0
                    
                # 2. Orientation (Sheet Flips)
                # In backward search, current_path[-1] is the most recently unfolded bend.
                # If the next bend has an opposite sign, it requires flipping the sheet on the press brake.
                if current_path and "diagnostics" in current_path[-1]:
                    last_angle = current_path[-1]["diagnostics"].get("signed_angle", 0)
                    if (last_angle > 0 and bend_rec.signed_angle < 0) or (last_angle < 0 and bend_rec.signed_angle > 0):
                        score += 5.0  # Penalty for flip
                        
                # 3. Bend Length (prefer unfolding longer, primary bends earlier)
                score -= bend_rec.length * 0.01
                
                return score

            def get_symmetry_signature(b_id: str) -> str:
                """Create a signature for a bend to detect symmetric sibling branches."""
                bend_rec = self.bend_graph.get_bend(b_id)
                if not bend_rec:
                    return b_id
                score = score_candidate(b_id)
                return f"{bend_rec.parent_panel_id}_{bend_rec.child_panel_id}_{bend_rec.signed_angle:.1f}_{bend_rec.length:.1f}_{score:.2f}"

            all_candidates = sorted(list(current.remaining_bends), key=score_candidate)
            
            # Topological & Symmetry Pre-filter
            candidates = []
            seen_signatures = set()
            for b_id in all_candidates:
                sig = get_symmetry_signature(b_id)
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    candidates.append(b_id)
                else:
                    self.metrics["prefilter_rejected"] += 1

            valid_successors = []
            
            for bend_id in candidates:
                bend_rec = self.bend_graph.get_bend(bend_id)
                if bend_rec is None:
                    continue

                # 1. Predictive cache / dead_states check
                next_fp = current.predict_successor_fingerprint(bend_id, panel_graph=self.panel_graph)
                if next_fp in dead_states:
                    self.metrics["dead_states_hit"] += 1
                    continue

                # 2. Validation Cache Check
                cache_key = (fingerprint, bend_id, toolset_hash, unknown_policy)
                if cache_key in self.validation_cache:
                    self.metrics["cache_hits"] += 1
                    val_result = self.validation_cache[cache_key]
                else:
                    self.metrics["occ_evaluations"] += 1
                    kwargs = {"tooling_override": tooling_override}
                    if unknown_policy != "PRODUCTION":
                        kwargs["unknown_policy"] = unknown_policy
                    else:
                        # Check if validator accepts unknown_policy
                        try:
                            import inspect
                            if "unknown_policy" in inspect.signature(self.validator.validate_step).parameters:
                                kwargs["unknown_policy"] = unknown_policy
                        except Exception:
                            pass
                    val_result = self.validator.validate_step(
                        current,
                        bend_rec,
                        self.panel_graph,
                        **kwargs
                    )
                    self.validation_cache[cache_key] = val_result

                if not val_result.valid:
                    self.rejection_records.append({
                        "bend_id": bend_id,
                        "stage": val_result.stage,
                        "reason": val_result.reason,
                        "depth": depth
                    })
                    continue

                if val_result.next_state is not None:
                    valid_successors.append((bend_id, val_result.next_state))

            # DFS Recurse on valid successors
            for bend_id, successor in valid_successors:
                moving_panels = list(self.panel_graph.moving_subtree(bend_id)) if self.panel_graph else []
                coll_raw = getattr(val_result, "collision_status", "VALID")
                c_stat = coll_raw.value if hasattr(coll_raw, "value") else str(coll_raw)
                tool_raw = getattr(val_result, "tooling_status", "SIMPLIFIED")
                t_stat = tool_raw.value if hasattr(tool_raw, "value") else str(tool_raw)
                align_raw = getattr(val_result, "alignment_status", "CANNOT_VERIFY")
                a_stat = align_raw.value if hasattr(align_raw, "value") else str(align_raw)
                action = {
                    "bend_id": bend_id,
                    "unfolded_angle": 0.0,
                    "moving_panels": moving_panels,
                    "step_number": len(current.remaining_bends),
                    "collision_status": c_stat,
                    "tooling_status": t_stat,
                    "alignment_status": a_stat,
                    "has_unknown": getattr(val_result, "has_unknown", False)
                }
                
                current_path.append(action)
                result_path = _dfs(successor, depth + 1, current_path)
                
                # Cleanup OCC Shape immediately if branch failed
                if result_path is None:
                    successor.clear_shape()
                    current_path.pop()
                else:
                    # Clear remaining unused valid successors to save memory
                    for b_id, s in valid_successors:
                        if s != successor:
                            s.clear_shape()
                    return result_path

            # Dead end reached for this fingerprint
            dead_states.add(fingerprint)
            current.clear_shape()
            return None

        # Execute DFS
        final_path = _dfs(initial_state, 0, [])

        if final_path is not None:
            forward_sequence = list(reversed(final_path))
            for i, step in enumerate(forward_sequence):
                step["step_number"] = i + 1

            seq_has_unknown = any(s.get("has_unknown", False) or s.get("collision_status") == "UNKNOWN" for s in forward_sequence)
            return {
                "success": True,
                "termination_reason": "GOAL_REACHED",
                "sequence": forward_sequence,
                "sequence_has_unknown": seq_has_unknown,
                "explored_count": self.metrics["explored_count"],
                "rejections_count": len(self.rejection_records),
                "dead_states_count": len(dead_states),
                "metrics": self.metrics,
                "cache_stats": self.cache.stats if self.cache else {}
            }

        term_reason = "NO_SOLUTION"
        if getattr(self, "timed_out", False):
            term_reason = "TIME_EXHAUSTED"
        elif getattr(self, "budget_exhausted", False):
            term_reason = "NODE_LIMIT_EXHAUSTED"

        return {
            "success": False,
            "termination_reason": term_reason,
            "error": f"DFS search finished with {term_reason} after exploring {self.metrics['explored_count']} states.",
            "explored_count": self.metrics["explored_count"],
            "rejections": self.rejection_records,
            "dead_states_count": len(dead_states),
            "metrics": self.metrics,
            "cache_stats": self.cache.stats if self.cache else {}
        }
