# Native FreeCAD Tasks lifetime and presentation

The SMBS workspace retains its own session and reusable controls. The native
TaskDialogPython wraps a QWidget form in FreeCAD's own TaskBox. Hiding that
form is not closure: minimise/restore, overlay collapse and Model/Tasks
switching must leave the form attached. Cleanup is explicit via reject,
native auto-close callbacks, deferred deletion, or synchronous destruction
fallback. The active native dialog's content is checked before closing it,
so unrelated workbench tasks are not closed by SMBS.

The form uses native Close, native group boxes, Qt size hints, and the native
TaskView scrollbar. Only the current stacked page contributes its size hint.
No change to simulation, tooling, search or PDF data. Implementation reference:
FreeCAD 1.1.0 src/Gui/TaskView/TaskDialogPython.cpp and TaskView.cpp; SheetMetal
taskLoadUI/taskAccept/taskReject; CAM toolbit task panel. These are interface
patterns, not copied source.

Headless tests exercise event handling and attachment ownership. Interactive
Windows/macOS FreeCAD acceptance: minimize/restore with Simulation and PDF
open; switch Model/Tasks; collapse native task group; close with Close or Esc;
reopen; switch to another task and close SMBS; verify no state loss or duplicate
panels. Verify long PDF/tooling controls scroll and fit at narrow dock widths.
