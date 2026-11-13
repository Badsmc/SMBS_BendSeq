"""Run an actual SMBS Manual native D6 benchmark INSIDE FreeCAD Python.

This is an active developer command, not a geometric simulator or mock.
Launch from FreeCADCmd or FreeCAD's Python console with run(...), using
the same genuine FreeCAD Part and installed SheetMetal Unfolder that the
workbench uses. The source FCStd and tooling files are read-only.

Example from FreeCAD Python console:
    import sys
    sys.path.insert(0, '/path/to/SMBS_BendSeq')
    from tools.run_native_benchmark import run
    run()  # repository demo/parts reference, tooling AP1017 and AT3015

Optional:
    run(part='/absolute/path/example.FCStd', output='/absolute/path/perf.json')
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PART = ROOT / "demo" / "parts" / "2-1-3-4-(ok).FCStd"
DEFAULT_PUNCH_ID = "PUNCH_AP_1017_88°_H104,65_R0,8"
DEFAULT_DIE_ID = "T-DIE_AT_3015_88°_V12_H80"
MANUAL_ORDER = (
    "B07", "B03", "B05", "B01", "B08",
    "B04", "B09", "B10", "B06", "B02",
)


def _desktop_dir() -> Path:
    from smbs_core.kinematics2d.developer_debug_log import desktop_directory
    return desktop_directory()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _user_tool_source(root: Path, kind: str, name: str) -> Path:
    """Exact tool FCStd matching the selected ToolLibrary ID."""
    directory = root / "toollibrary" / kind
    source = directory / (name + ".FCStd")
    if not source.is_file():
        raise FileNotFoundError(
            "Selected native {} geometry missing: {}".format(kind, source)
        )
    return source


def _snapshot(part: Path, punch: Path, die: Path,
              config: Any) -> dict[str, Any]:
    """Record exact input provenance. Do not infer Git HEAD from a ZIP."""
    import FreeCAD as App
    try:
        freecad_version = ".".join(map(str, App.Version()))
    except Exception:
        freecad_version = "NOT_RECORDED"
    try:
        occ_version = str(App.OCC_VERSION)
    except Exception:
        occ_version = "NOT_RECORDED"
    return {
        "schema_version": "smbs.native_manual_benchmark.v1",
        "part": {"path": str(part), "sha256": sha256_file(part)},
        "punch": {"path": str(punch), "sha256": sha256_file(punch)},
        "die": {"path": str(die), "sha256": sha256_file(die)},
        "runtime": {
            "freecad": freecad_version,
            "occ": occ_version,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "script_path": str(Path(__file__).resolve()),
            "source_root": str(ROOT),
            # Actual current FreeCAD module path is checked below.
        },
        "manual_order": list(MANUAL_ORDER),
        "reverse_axis_by_bend": {bend: True for bend in MANUAL_ORDER},
        "match_active_bend_length": True,
        "allow_end_zone_collision": True,
        "end_zone_length_mm": 10.0,
        "adaptive_config": dict(vars(config)),
        "physical_certification": (
            "SAMPLED_ONLY_NOT_CONTINUOUS; "
            "TRANSFER_BACKGAUGE_CLAMPS_FRAME_NOT_VERIFIED"
        ),
    }


def run(*, part: str | Path = DEFAULT_PART,
        output: str | Path | None = None,
        punch_id: str = DEFAULT_PUNCH_ID,
        die_id: str = DEFAULT_DIE_ID,
        unfold_name: str = "Body_Unfold",
        match_active_bend_length: bool = True,
        allow_end_zone_collision: bool = True,
        end_zone_length_mm: float = 10.0,
        reverse_axis: bool = True,
        max_phase_time_s: float = 300.0,
        max_states_per_phase: int = 64,
        component_shadow_samples_per_bend: int = 0) -> dict[str, Any]:
    """Run the real native oracle on the specified FCStd with exact tool IDs.

    Executed in FreeCAD's Python interpreter, not ordinary system Python.
    Calls the same Manual production service as the GUI. Fail closed on
    missing Unfold, native tooling, SheetMetal or any physical error.
    """
    try:
        import FreeCAD as App
    except ImportError as exc:
        raise RuntimeError(
            "This benchmark must run INSIDE FreeCAD Python / FreeCADCmd"
        ) from exc

    from freecad_smbs.commands.pressbrake_2d import build_part_session
    from smbs_core.kinematics2d.tooling_25d import (
        ToolLibrary25D, ToolingSetup2D, ToolKind, ValidationStatus,
    )
    from smbs_core.kinematics2d.motion_trajectory_3d import (
        AdaptiveSamplingConfig,
    )
    from smbs_core.kinematics2d.manual_physical_validation import (
        check_manual_3d_sequence,
    )

    part_path = Path(part).expanduser().resolve(strict=True)
    punch_path = _user_tool_source(ROOT, "punch", punch_id)
    die_path = _user_tool_source(ROOT, "die", die_id)
    result_path = (
        Path(output).expanduser().resolve()
        if output is not None
        else _desktop_dir() / "SMBS_native_benchmark.json"
    )
    if result_path in (
        part_path, punch_path, die_path,
        Path(__file__).resolve(),
    ):
        raise ValueError("Benchmark output cannot overwrite source geometry")
    if not (0.0 <= float(end_zone_length_mm) < 10000.0):
        raise ValueError("Invalid conditional end-zone length")
    # Optional component pre-fuse shadow measurements cannot change the
    # native verifier's verdict. Never enable expensive probes implicitly.
    shadow_value = os.environ.get(
        "SMBS_COMPONENT_SHADOW_SAMPLES",
        str(component_shadow_samples_per_bend),
    )
    try:
        shadow_requested = int(shadow_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("component shadow sample count must be an integer") from exc
    if not 0 <= shadow_requested <= 8:
        raise ValueError("component shadow sample count must be 0..8")
    cfg = AdaptiveSamplingConfig(
        max_phase_time_s=float(max_phase_time_s),
        max_evaluated_states_per_phase=int(max_states_per_phase),
        component_shadow_samples_per_bend=shadow_requested,
    )
    record = _snapshot(part_path, punch_path, die_path, cfg)
    record["match_active_bend_length"] = bool(match_active_bend_length)
    record["allow_end_zone_collision"] = bool(allow_end_zone_collision)
    record["end_zone_length_mm"] = float(end_zone_length_mm)
    record["reverse_axis_by_bend"] = {
        bend: bool(reverse_axis) for bend in MANUAL_ORDER
    }

    existing = set(App.listDocuments())
    previous = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    original_doc = None
    start = perf_counter()
    try:
        original_doc = App.openDocument(str(part_path), True)
        selected = original_doc.getObject(unfold_name)
        if selected is None:
            raise RuntimeError(
                "SheetMetal Unfold {} not present in input FCStd; "
                "pass correct unfold_name".format(unfold_name)
            )
        session = build_part_session(original_doc, [selected])
        if set(session.bend_order) != set(MANUAL_ORDER):
            raise RuntimeError(
                "Reference bend IDs do not match real SheetMetal Unfold"
            )
        session.set_bend_order(MANUAL_ORDER)

        library = ToolLibrary25D(ROOT / "toollibrary")
        punch = library.load_tool(punch_id, ToolKind.PUNCH)
        die = library.load_tool(die_id, ToolKind.DIE)
        if (punch.validation_status != ValidationStatus.GEOMETRY_VERIFIED
                or die.validation_status != ValidationStatus.GEOMETRY_VERIFIED):
            raise RuntimeError("Tools lack GEOMETRY_VERIFIED native datums")
        setup = ToolingSetup2D()
        setup.select_punch(punch)
        setup.select_die(die)
        setup.set_match_active_bend_length(bool(match_active_bend_length))
        setup.allow_end_zone_collision = bool(allow_end_zone_collision)
        setup.end_zone_length_mm = float(end_zone_length_mm)
        first_bend = session.fold_state.bends[MANUAL_ORDER[0]]
        setup.align_to_cross_section(
            bend_id=MANUAL_ORDER[0],
            bend_zone=first_bend.bend_zone,
            thickness_mm=float(first_bend.bend_zone.thickness_mm),
        )

        # Production Manual native D6, including shared geometry cache
        # instrumentation and the normal local Desktop debug journal.
        record["runtime"]["loaded_manual_service"] = str(
            Path(sys.modules[
                check_manual_3d_sequence.__module__
            ].__file__).resolve()
        )
        result = check_manual_3d_sequence(
            session, setup, MANUAL_ORDER,
            match_active_bend_length=bool(match_active_bend_length),
            allow_end_zone_collision=bool(allow_end_zone_collision),
            end_zone_length_mm=float(end_zone_length_mm),
            reverse_axis_by_bend=record["reverse_axis_by_bend"],
            config=cfg,
        )
        record["result"] = result
    except Exception as exc:
        record["result"] = {
            "status": "UNVERIFIED",
            "reason": "benchmark_exception:{}:{}".format(
                type(exc).__name__, exc
            ),
            "sampled_3d_only": True,
            "continuous_motion_certified": False,
        }
    finally:
        record["wall_elapsed_s"] = round(perf_counter() - start, 6)
        if original_doc is not None and original_doc.Name not in existing:
            App.closeDocument(original_doc.Name)
        if previous and App.getDocument(previous) is not None:
            App.setActiveDocument(previous)

    # The production Manual result may contain unexpected FreeCAD-native
    # objects in error diagnostics. Serialize conservatively, exactly as the
    # existing Desktop journal does, instead of crashing after the solver.
    from smbs_core.kinematics2d.developer_debug_log import _jsonable
    safe_record = _jsonable(record)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(safe_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("SMBS native benchmark: {} ({:.3f} s)".format(
        safe_record["result"]["status"], safe_record["wall_elapsed_s"],
    ))
    print("Report: {}".format(result_path))
    return safe_record


if __name__ == "__main__":
    # FreeCADCmd execution accepts the default packaged reference geometry.
    # Other settings can be passed from a FreeCAD Python console by importing
    # run() and supplying keyword arguments.
    run()
