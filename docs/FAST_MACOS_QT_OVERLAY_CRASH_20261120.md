# FAST dialog / macOS Qt6 native crash — 2026-11-20

## Evidence

Source: operator-supplied `SMBS_debug_log(20261120-193448).json`,
`SMBS_native_crash(9).log` and macOS full crash report.
macOS 12.7.6, Intel, Python 3.11, Qt 6.8.3.

Event timeline (UTC, last session):

- `19:31:38`: prior machine playback completed.
- `19:31:40`: native FreeCAD Tasks page rehosted through
  `Gui.Control.showDialog`; widgets get `QT_LIFETIME_EVENT`.
- `19:33:17`: FAST returns 5 candidates in 0.382 s (252 sampled
  axis screens, 142 rejected branches). Algorithm has already returned.
- `19:33:58`: when applying the selected candidate,
  `_on_staged_plan_changed -> _populate_operations ->
  _format_operation_row -> _backgauge_preview_position ->
  _sequenced_gauge_envelopes -> build_sheet_preview_faces`
  is active. Document observers show CAD document and
  `SMBS_Animation_01` being deleted.
- Final recorded exception:
  `RuntimeError: Internal C++ object (PySide6.QtWidgets.QLabel)
  already deleted` when `_update_tooling_status_label()` tries
  writing to `tooling_status`.
- Native crash report: main thread `SIGSEGV`, through
  `Gui::OverlayManager::Private::onTimer()` called by Qt6
  `QTimer::timerEvent()`; Python's faulthandler reports no Python
  stack on the native crash thread.

**Important:** the deletion events/RuntimeError may be consequences
of an in-progress native crash, rather than its initiating cause.
The evidence implicates FreeCAD Qt6 OverlayManager and application
lifetime handling during synchronous apply; it cannot prove the
exact lower-level C++ invalid pointer.

## Mitigations in manualmod.ui

- FAST chooser uses `QDialog.open()` (not `exec()`) and is
  parented to the FreeCAD main window, not to a disposable Tasks root.
  The accepted selection is captured before the dialog is deleted.
- FAST application runs on the next Qt event-loop iteration,
  not inside the chooser's nested modal event loop.
- Before applying, verify live workspace, timer, task stack, tree,
  flat viewer and status widgets and that the session and operations
  have not changed.
- During FAST application, building the operation rows skips the
  expensive synchronous automatic CAD backgauge envelope computation.
  The normal viewer can populate the envelope once; if the matching
  cached result is ready, the table restores its values without
  recomputing.
- The staged refresh and per-row table code revalidate Qt objects
  **after** slow computations and before touching widgets; signal
  unblocking is guarded against destroyed C++ objects.
- Diagnostics now distinguish: `FAST_RESULT_DIALOG_OPENED`,
  `FAST_RESULT_DIALOG_FINISHED`, `FAST_APPLY_STARTED`,
  `FAST_CANDIDATE_APPLIED`, `FAST_APPLY_SKIPPED`,
  `FAST_APPLY_HOST_DESTROYED`, `OPERATIONS_REFRESH_ABORTED`.

## Manual acceptance test (mandatory; not covered by Python CI)

1. Update `manualmod.ui`; exit and relaunch FreeCAD (do not
   load duplicate copies of the add-on from several Mod directories).
2. Open a COPY of the same 10-bend FCStd; choose Simulations/Tasks.
3. Reset, FAST, select a candidate, Apply, then repeat at least
   5 times in a row with changed selection. Test Cancel separately.
4. Confirm no native task panel disappears and no C++ widget
   already-deleted error, no document closes, playback and Ctrl+S work.
5. Repeat with automatic CAD gauge enabled, and with it disabled,
   to isolate expensive preview interaction.
6. Repeat with FreeCAD **View -> Overlay Docked Panel** overlay
   disabled, as a diagnostic comparison, if the host native crash
   persists. The add-on must not silently change the user's
   global FreeCAD preference.
7. Compare the last debug events with the native crash log. If
   `OverlayManager::Private::onTimer()` still crashes even with
   nonblocking FAST chooser, report separately against FreeCAD
   with this native stack and a minimized reproducer.

CI tests are headless/static GUI contracts, not a macOS Qt6 integration
test. A passing CI result is **not** confirmation that the native
FreeCAD bug is eliminated.
