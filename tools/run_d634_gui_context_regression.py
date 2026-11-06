"""Native FreeCAD D6.3.4 multi-fixture GUI-context regression.

Execute in a native FreeCAD Python runtime, not ordinary CPython CI:
    exec(open("/Users/badmc/Desktop/SMBS_BendSeq/tools/run_d634_gui_context_regression.py").read())

Checks the real diagnostic viewer's underlying geometry after L -> Z -> 135 -> L
switches, including B01's completed angle during B02. No source files are saved.
"""
from __future__ import annotations

import hashlib
import json
import runpy
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from smbs_core.report_paths import report_file

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    script = ROOT / "tools" / "freecad_gui_d633_diagnostic.py"
    namespace = runpy.run_path(str(script))
    inspect = namespace["inspect_trajectory"]
    display = namespace["show_sample"]
    runtime = inspect.__globals__

    checks = []
    cases = [
        ("L-(ok).FCStd", "B01", (0, 25, 50)),
        ("Z-(ok).FCStd", "B02", (0, 25, 50)),
        ("135-(ok).FCStd", "B02", (0, 25, 40)),
        ("L-(ok).FCStd", "B01", (25,)),
    ]
    files = {
        filename: runtime["resolve_fixture_path"](filename)
        for filename, _, _ in cases
    }
    hashes_before = {k: sha256_file(v) for k, v in files.items()}
    started = time.perf_counter()
    for filename, bend_id, sample_indices in cases:
        inspect(filename, bend_id)
        context = runtime["ACTIVE_TRAJECTORY_CONTEXT"]
        assert context is not None and context["fixture"] == filename
        assert context["bend_id"] == bend_id
        assert bend_id in context["state"].bends
        for index in sample_indices:
            display(index)
            selected = context["samples"][index]
            wp = runtime["wp_feat"].Shape
            assert not wp.isNull() and wp.isValid() and wp.Volume > 0
            assert selected.active_bend_id == bend_id
            if bend_id == "B02":
                assert "B01" in selected.completed_angles
                assert abs(selected.completed_angles["B01"]) > 0
            assert context["state"].angles == {b: 0.0 for b in context["state"].bends}
            checks.append({
                "fixture": filename,
                "bend": bend_id,
                "sample": index,
                "phase": selected.phase,
                "signed_angle_deg": selected.signed_angle_deg,
                "prior_completed_angles": selected.completed_angles,
                "workpiece_volume_mm3": wp.Volume,
                "safe": selected.is_safe,
            })
    hashes_after = {k: sha256_file(v) for k, v in files.items()}
    if hashes_after != hashes_before:
        raise RuntimeError("Original FCStd fixture was modified during diagnostic viewing")
    report = {
        "schema": "smbs-d634-gui-context-regression-v1",
        "result": "PASS_NATIVE_FREECAD",
        "cases": checks,
        "fixture_hashes_sha256": hashes_after,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "gui_visual_confirmed": False,
    }
    output = report_file("d634_gui_context_regression.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"D6.3.4 native viewer-context regression: PASS ({len(checks)} samples). Report: {output}")

if __name__ == "__main__":
    main()
