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
