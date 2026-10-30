"""
manual_runner.py - Public entry point for HandMod execution on FCStd models.

Exports:
- run_manual_sequence_on_fcstd(fcstd_path, manual_input, ...)
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional, Union

from freecad_smbs.manual.manual_worker import (
    ManualSequenceInput,
    ManualSequenceResult,
    ManualSequenceWorker,
)


def run_manual_sequence_on_fcstd(
    fcstd_path: Union[str, Path],
    manual_input: ManualSequenceInput,
    *,
    out_runs_dir: Union[str, Path, None] = None,
    library_path: Union[str, Path, None] = None,
    tooling_set_id: Optional[str] = None,
    cancel_token: Optional[threading.Event] = None,
    phases: Optional[List[str]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ManualSequenceResult:
    """
    Public entry point to run manual bend sequencing pipeline on an FCStd model.
    In Phase 4a, executes mini-pipeline up to Stage 3 (04 creation & bend_id resolution).
    """
    worker = ManualSequenceWorker()
    return worker.execute_mini_pipeline(
        fcstd_path,
        manual_input,
        out_runs_dir=out_runs_dir,
        cancel_token=cancel_token,
        phases=phases,
        progress_callback=progress_callback,
    )


if __name__ == "__main__":
    import argparse
    import pickle
    import sys

    parser = argparse.ArgumentParser(description="Run SMBS manual sequence in isolated headless process")
    parser.add_argument("--payload", required=True, help="Path to input pickle payload")
    parser.add_argument("--output", required=True, help="Path to output pickle result")
    args = parser.parse_args()

    with open(args.payload, "rb") as f:
        payload = pickle.load(f)

    def _cli_progress(step_idx: int, total_steps: int, bend_id: str) -> None:
        sys.stderr.write(f"PROGRESS:{step_idx}:{total_steps}:{bend_id}\n")
        sys.stderr.flush()

    res = run_manual_sequence_on_fcstd(
        fcstd_path=payload["fcstd_path"],
        manual_input=payload["manual_input"],
        out_runs_dir=payload.get("out_runs_dir"),
        progress_callback=_cli_progress,
    )

    with open(args.output, "wb") as f:
        pickle.dump(res, f)

