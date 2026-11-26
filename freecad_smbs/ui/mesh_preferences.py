"""FreeCAD-owned persistent defaults for Mesh verification.

The actual per-run inputs remain editable in Tasks. Existing QSettings
values are imported only when the corresponding native parameter is absent.
"""
from __future__ import annotations

import math

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/BendSeq/Mesh"
DEFAULT_BUDGET = 60
DEFAULT_OVERLAP = 0.0001


def _group():
    import FreeCAD as App
    return App.ParamGet(PARAM_PATH)


def _valid_budget(value):
    return 1 <= value <= 3600


def _valid_overlap(value):
    return math.isfinite(value) and 0.000001 <= value <= 1000.0


def migrate_mesh_preferences():
    from freecad_smbs.ui.native_preferences import NativePreferences
    prefs = NativePreferences()
    budget = prefs.value("mesh/budget_seconds", DEFAULT_BUDGET, type=int)
    overlap = prefs.value("mesh/min_overlap_mm3", DEFAULT_OVERLAP, type=float)
    if not _valid_budget(budget):
        prefs.setValue("mesh/budget_seconds", DEFAULT_BUDGET)
    if not _valid_overlap(overlap):
        prefs.setValue("mesh/min_overlap_mm3", DEFAULT_OVERLAP)


def mesh_defaults():
    group = _group()
    budget = group.GetInt("BudgetSeconds", DEFAULT_BUDGET)
    overlap = group.GetFloat("MinimumOverlapMM3", DEFAULT_OVERLAP)
    return (budget if _valid_budget(budget) else DEFAULT_BUDGET,
            overlap if _valid_overlap(overlap) else DEFAULT_OVERLAP)


def save_mesh_defaults(budget, overlap):
    budget, overlap = int(budget), float(overlap)
    if not _valid_budget(budget) or not _valid_overlap(overlap):
        raise ValueError("Invalid mesh verification settings")
    group = _group()
    group.SetInt("BudgetSeconds", budget)
    group.SetFloat("MinimumOverlapMM3", overlap)
