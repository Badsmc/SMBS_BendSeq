"""SMBS optional icon theme; Qt SVG assets have no runtime dependencies."""
from pathlib import Path

_ORIGINAL = Path(__file__).resolve().parent / "resources" / "icons"
_SELECTED = _ORIGINAL / "selected"
_NAMES = frozenset(["gui_part","gui_tooling","gui_simulation","gui_sequence_catalog","gui_manual","gui_settings","control_add","control_unfold","control_dxf","control_select_bend","control_prev","control_validate","control_mesh","control_accuracy","control_collision","control_collision_export","control_play","control_pause","control_step_back","control_step_forward","control_reset","control_rotate","tooling","control_save"])

def icon_path(name: str) -> Path:
    """Return the themed icon when available, otherwise the original."""
    name = Path(name).name
    if name.endswith(".svg") and name[:-4] in _NAMES:
        replacement = _SELECTED / name
        if replacement.is_file():
            return replacement
    return _ORIGINAL / name
