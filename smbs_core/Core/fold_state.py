"""
fold_state.py - Full Canonical Physical State Model for Sheet Metal Folding (SMBS Phase 3)

SPEC v1.0 Requirement (LAW-3 — FoldState):
State MUST NOT be modeled merely as shape + remaining_bends.
Canonical FoldState contains:
├── shape
├── folded_bends
├── remaining_bends
├── panel_transforms
├── bend_transforms
├── orientation
├── tooling_state
└── diagnostics
"""

import hashlib
import json
from typing import Dict, Any, List, Set, Tuple, Optional
from smbs_core.Core.bend_graph import BendRecord, BendGraph
from smbs_core.Core.panel_graph import PanelGraph


class SearchState:
    """
    Lightweight Search Node representation for graph search algorithms.
    Does NOT hold heavy OCC TopoShapes.
    """
    def __init__(
        self,
        state_id: str,
        parent_id: Optional[str],
        folded_bends: Set[str],
        remaining_bends: Set[str],
        bend_transforms: Dict[str, float],
        g_cost: float = 0.0,
        action: Optional[Dict[str, Any]] = None
    ):
        self.state_id = state_id
        self.parent_id = parent_id
        self.folded_bends = set(folded_bends)
        self.remaining_bends = set(remaining_bends)
        self.bend_transforms = dict(bend_transforms)
        self.g_cost = g_cost
        self.action = action or {}

    def is_goal(self) -> bool:
        return len(self.remaining_bends) == 0


class FoldState:
    """
    Canonical physical state of a sheet metal part during folding/unfolding search.
    """

    def __init__(
        self,
        shape: Any,
        folded_bends: Set[str],
        remaining_bends: Set[str],
        panel_transforms: Optional[Dict[str, Tuple[float, ...]]] = None,
        bend_transforms: Optional[Dict[str, float]] = None,
        orientation: Optional[Tuple[float, float, float]] = None,
        tooling_state: Optional[Dict[str, Any]] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None
    ):
        self.shape = shape
        self.folded_bends = set(folded_bends)
        self.remaining_bends = set(remaining_bends)
        self.panel_transforms = panel_transforms or {}
        self.bend_transforms = bend_transforms or {}
        self.orientation = orientation or (0.0, 0.0, 0.0)
        self.tooling_state = tooling_state or {"die_id": "DEFAULT_DIE", "punch_id": "DEFAULT_PUNCH"}
        self.diagnostics = diagnostics or {}
        self.parent_id = parent_id

        # New: Track dynamic 3D bend axes
        self.bend_axes = getattr(self, 'bend_axes', {})
        self.bend_parent_panels = getattr(self, 'bend_parent_panels', {})
        
        self._cached_fingerprint: Optional[str] = None

    def clear_shape(self) -> None:
        """Explicitly release OCC TopoShape reference to free C++ memory immediately."""
        self.shape = None

    @classmethod
    def initial(
        cls,
        part_shape: Any,
        bend_graph: BendGraph,
        panel_graph: Optional[PanelGraph] = None,
        tooling_state: Optional[Dict[str, Any]] = None
    ) -> 'FoldState':
        """
        Construct initial Root state (Fully Bent 3D Part, 0 bends unfolded).
        """
        all_bends = {b.id for b in bend_graph.all_bends()}
        bend_angles = {b.id: b.signed_angle for b in bend_graph.all_bends()}
        panel_ids = list(panel_graph.panels.keys()) if panel_graph else []
        initial_panel_transforms = {pid: (0.0, 0.0, 0.0) for pid in panel_ids}

        initial_bend_axes = {}
        initial_bend_parent_panels = {}
        for b in bend_graph.all_bends():
            initial_bend_axes[b.id] = (b.axis_origin, b.axis_direction)
            initial_bend_parent_panels[b.id] = b.parent_panel_id

        state = cls(
            shape=part_shape,
            folded_bends=all_bends,
            remaining_bends=all_bends,
            panel_transforms=initial_panel_transforms,
            bend_transforms=bend_angles,
            tooling_state=tooling_state
        )
        state.bend_axes = initial_bend_axes
        state.bend_parent_panels = initial_bend_parent_panels
        return state

    def is_goal(self) -> bool:
        """Goal is reached when 0 bends remain in folded state (kinematic flat state)."""
        return len(self.remaining_bends) == 0

    def is_technologically_flat_goal(
        self,
        backend: Optional[Any] = None,
        total_bend_allowance_area: float = 0.0,
        area_s0: Optional[float] = None,
        tolerance: float = 1e-2
    ) -> Dict[str, Any]:
        """
        Strict technological flat pattern acceptance criterion:
        1. cylindrical_faces == 0
        2. is_100_percent_flat == True (len(self.remaining_bends) == 0)
        3. Area(S_flat) == Area(S0) + sum(bend_allowance_area)
        """
        rem_flat = (len(self.remaining_bends) == 0)
        cyl_count = 0
        area_flat = 0.0
        area_valid = True

        if backend is not None and self.shape is not None and hasattr(backend, 'get_faces'):
            faces = backend.get_faces(self.shape)
            for f in faces:
                stype = backend.get_surface_type(f)
                if stype == "cylinder":
                    cyl_count += 1

            if area_s0 is not None and hasattr(backend, 'get_surface_area'):
                area_flat = backend.get_surface_area(self.shape)
                expected_area = area_s0 + total_bend_allowance_area
                area_valid = abs(area_flat - expected_area) < tolerance

        cyl_valid = (cyl_count == 0)
        is_true_technological_goal = (rem_flat and cyl_valid and area_valid)

        return {
            "is_true_technological_goal": is_true_technological_goal,
            "is_100_percent_flat": rem_flat,
            "cylindrical_faces": cyl_count,
            "area_flat": area_flat,
            "area_valid": area_valid
        }

    def fingerprint(self) -> str:
        """
        SPEC v1.0 Requirement: Canonical physical state fingerprint.
        Incorporates folded_bends, remaining_bends, canonical panel_transforms,
        bend_transforms, orientation, and tooling_state.
        Does NOT depend on OCC Shape objects for predictive hashing and state deduplication.
        """
        if self._cached_fingerprint is not None:
            return self._cached_fingerprint

        folded_sig = "_".join(sorted(list(self.folded_bends)))
        remaining_sig = "_".join(sorted(list(self.remaining_bends)))

        bend_trans_sig = "_".join(f"{k}:{self.bend_transforms[k]:.1f}" for k in sorted(self.bend_transforms.keys()))
        
        # Canonical panel transforms signature with fixed rounding precision (0.01 mm / deg)
        panel_trans_parts = []
        for p_id in sorted(self.panel_transforms.keys()):
            val = self.panel_transforms[p_id]
            if isinstance(val, (tuple, list)):
                formatted_val = ",".join(f"{v:.2f}" for v in val)
            else:
                formatted_val = f"{val}"
            panel_trans_parts.append(f"{p_id}:({formatted_val})")
        panel_trans_sig = "_".join(panel_trans_parts)

        orient_sig = f"{self.orientation[0]:.1f}_{self.orientation[1]:.1f}_{self.orientation[2]:.1f}"
        tooling_sig = f"{self.tooling_state.get('punch_id')}_{self.tooling_state.get('die_id')}"
        
        axes_parts = []
        for b_id in sorted(self.bend_axes.keys()):
            orig, dir_ = self.bend_axes[b_id]
            axes_parts.append(f"{b_id}:({orig[0]:.2f},{orig[1]:.2f},{orig[2]:.2f}|{dir_[0]:.3f},{dir_[1]:.3f},{dir_[2]:.3f})")
        axes_sig = "_".join(axes_parts)

        raw_key = (
            f"folded[{folded_sig}]_rem[{remaining_sig}]_bends[{bend_trans_sig}]_"
            f"panels[{panel_trans_sig}]_orient[{orient_sig}]_tool[{tooling_sig}]_axes[{axes_sig}]"
        )
        self._cached_fingerprint = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        return self._cached_fingerprint

    def predict_successor_fingerprint(
        self,
        bend_id: str,
        moving_panels: Optional[Set[str]] = None,
        panel_graph: Optional[Any] = None
    ) -> str:
        """
        Predict the fingerprint of the successor state if bend_id were unfolded.
        This allows checking caches/dead_states BEFORE executing expensive OCC geometry.
        """
        if moving_panels is None and panel_graph is not None:
            moving_panels = panel_graph.moving_subtree(bend_id)
            
        new_folded = set(self.folded_bends) - {bend_id}
        new_remaining = set(self.remaining_bends) - {bend_id}
        
        folded_sig = "_".join(sorted(list(new_folded)))
        remaining_sig = "_".join(sorted(list(new_remaining)))

        # Next bend transforms will have bend_id = 0.0
        new_bend_transforms = dict(self.bend_transforms)
        new_bend_transforms[bend_id] = 0.0
        bend_trans_sig = "_".join(f"{k}:{new_bend_transforms[k]:.1f}" for k in sorted(new_bend_transforms.keys()))
        
        panel_trans_parts = []
        for p_id in sorted(self.panel_transforms.keys()):
            val = self.panel_transforms[p_id]
            if isinstance(val, (tuple, list)):
                formatted_val = ",".join(f"{v:.2f}" for v in val)
            else:
                formatted_val = f"{val}"
            panel_trans_parts.append(f"{p_id}:({formatted_val})")
        panel_trans_sig = "_".join(panel_trans_parts)

        orient_sig = f"{self.orientation[0]:.1f}_{self.orientation[1]:.1f}_{self.orientation[2]:.1f}"
        tooling_sig = f"{self.tooling_state.get('punch_id')}_{self.tooling_state.get('die_id')}"
        
        new_bend_axes = dict(self.bend_axes)
        if bend_id in self.bend_axes and moving_panels:
            from smbs_core.Core.bend_transform import BendTransform
            rot_origin, rot_dir = self.bend_axes[bend_id]
            rot_angle = -self.bend_transforms.get(bend_id, 0.0)
            for b_id in self.remaining_bends:
                if b_id == bend_id:
                    continue
                parent_p = self.bend_parent_panels.get(b_id)
                if parent_p in moving_panels:
                    old_orig, old_dir = new_bend_axes[b_id]
                    new_orig = BendTransform.transform_point(old_orig, rot_origin, rot_dir, rot_angle)
                    new_dir = BendTransform.transform_vector(old_dir, rot_dir, rot_angle)
                    new_bend_axes[b_id] = (new_orig, new_dir)

        axes_parts = []
        for b_id in sorted(new_bend_axes.keys()):
            orig, dir_ = new_bend_axes[b_id]
            axes_parts.append(f"{b_id}:({orig[0]:.2f},{orig[1]:.2f},{orig[2]:.2f}|{dir_[0]:.3f},{dir_[1]:.3f},{dir_[2]:.3f})")
        axes_sig = "_".join(axes_parts)

        raw_key = (
            f"folded[{folded_sig}]_rem[{remaining_sig}]_bends[{bend_trans_sig}]_"
            f"panels[{panel_trans_sig}]_orient[{orient_sig}]_tool[{tooling_sig}]_axes[{axes_sig}]"
        )
        return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

    def apply_unfold(
        self,
        bend_id: str,
        unfolded_shape: Any,
        moving_panels: Set[str]
    ) -> 'FoldState':
        """
        Create successor FoldState after unfolding bend_id to 0 degrees.
        """
        new_folded = set(self.folded_bends) - {bend_id}
        new_remaining = set(self.remaining_bends) - {bend_id}

        new_bend_transforms = dict(self.bend_transforms)
        new_bend_transforms[bend_id] = 0.0  # Unbent to 0 degrees

        new_panel_transforms = dict(self.panel_transforms)
        for pid in moving_panels:
            if pid in new_panel_transforms:
                current_transform = new_panel_transforms[pid]
                if isinstance(current_transform, tuple) and len(current_transform) == 3:
                    new_panel_transforms[pid] = (
                        current_transform[0],
                        current_transform[1],
                        current_transform[2]
                    )

        # Update bend axes dynamically
        from smbs_core.Core.bend_transform import BendTransform
        new_bend_axes = dict(self.bend_axes)
        
        # We need the axis of the CURRENT bend to rotate the others
        rot_origin, rot_dir = self.bend_axes[bend_id]
        # In backward unfolding, we rotate by -signed_angle
        rot_angle = -self.bend_transforms[bend_id]
        
        for b_id in self.remaining_bends:
            if b_id == bend_id:
                continue
            # If this bend's parent panel is moving, the entire bend moves!
            parent_p = self.bend_parent_panels.get(b_id)
            if parent_p in moving_panels:
                old_orig, old_dir = new_bend_axes[b_id]
                new_orig = BendTransform.transform_point(old_orig, rot_origin, rot_dir, rot_angle)
                new_dir = BendTransform.transform_vector(old_dir, rot_dir, rot_angle)
                new_bend_axes[b_id] = (new_orig, new_dir)

        state = FoldState(
            shape=unfolded_shape,
            folded_bends=new_folded,
            remaining_bends=new_remaining,
            panel_transforms=new_panel_transforms,
            bend_transforms=new_bend_transforms,
            orientation=self.orientation,
            tooling_state=self.tooling_state,
            parent_id=self.fingerprint()
        )
        state.bend_axes = new_bend_axes
        state.bend_parent_panels = self.bend_parent_panels
        return state

    def __repr__(self) -> str:
        return f"<FoldState rem={len(self.remaining_bends)} folded={len(self.folded_bends)}>"
