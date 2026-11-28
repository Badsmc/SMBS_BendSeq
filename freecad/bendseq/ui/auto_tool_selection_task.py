"""Native contextual shortlist. No FreeCAD/Qt calls in the search worker."""
from math import dist
from queue import Queue, Empty
from threading import Event, Thread

from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary
from freecad.bendseq.core.kinematics2d.auto_tool_selection import BendRequirement, shortlist
from freecad.bendseq.core.kinematics2d.shared_strokes import stroke_axis
from freecad.bendseq.ui.localization import tr


def _snapshot(workspace):
    session = workspace.session
    bends = session.fold_state.bends
    requirements = tuple(BendRequirement(bid, dist(*bends[bid].axis_xyz),
                         session.local_bend_dimensions_mm.get(bid))
                         for bid in session.canonical_order)
    strokes = workspace._continuous_tool_strokes(workspace._tool_assembly_order())
    span = max(dist(*stroke_axis(group, bends)) for group in strokes)
    catalog = ToolingLibrary(workspace.tool_library.library_root).list_tools()
    return (session, requirements, span, catalog, workspace._current_clamp_config(),
            tuple(session.bend_order))


def show_auto_tool_selection(workspace, qt, embed, tool_kind):
    QtCore, _, widgets = qt()
    dialog = widgets.QDialog(workspace)
    layout = widgets.QVBoxLayout(dialog)
    note = widgets.QLabel(tr("Preliminary inventory shortlist only. Run FAST and Mesh after selecting tools."))
    note.setWordWrap(True)
    layout.addWidget(note)
    search = widgets.QPushButton(tr("Search"))
    stop = widgets.QPushButton(tr("Stop"))
    stop.setEnabled(False)
    layout.addWidget(search)
    layout.addWidget(stop)
    choices = widgets.QComboBox()
    layout.addWidget(choices)
    details = widgets.QPlainTextEdit()
    details.setReadOnly(True)
    layout.addWidget(details)
    apply = widgets.QPushButton(tr("Apply tool pair"))
    apply.setEnabled(False)
    layout.addWidget(apply)
    timer = QtCore.QTimer(dialog)
    timer.setInterval(100)
    state = {"snapshot": None, "result": None, "cancel": None, "queue": None}

    def selected():
        result = state["result"]
        index = choices.currentIndex()
        return result.candidates[index] if result is not None and 0 <= index < len(result.candidates) else None

    def refresh(*_):
        candidate = selected()
        result = state["result"]
        apply.setEnabled(candidate is not None and not timer.isActive())
        if result is None:
            return
        lines = [tr("Candidate pairs examined") + ": " + str(result.examined),
                 tr("Search complete") if result.complete else tr("Search limited or cancelled")]
        if candidate is not None:
            lines += [candidate.status, *candidate.warnings]
            for kind in ("punch", "die"):
                segments = candidate.plan[kind + "_segments"]
                lines.append(tr(kind) + ": " + " + ".join(
                    f"{seg['length_mm']:g}" for seg in segments) + " mm")
        lines += ["", tr("Rejected tools / pairs")]
        lines += [" / ".join(filter(None, (p, d))) + ": " + reason
                  for p, d, reason in result.rejected[:100]]
        details.setPlainText("\n".join(lines))

    def poll():
        try:
            result, error = state["queue"].get_nowait()
        except Empty:
            return
        timer.stop()
        search.setEnabled(True)
        stop.setEnabled(False)
        if error:
            details.setPlainText(error)
            return
        state["result"] = result
        for c in result.candidates:
            opening = "?" if c.opening_mm is None else f"{c.opening_mm:g}"
            choices.addItem(f"{c.punch_id} / {c.die_id} | V {opening} mm")
        refresh()

    def start(*_):
        if timer.isActive():
            return
        state["result"] = None
        choices.clear()
        apply.setEnabled(False)
        try:
            snap = _snapshot(workspace)
            if not snap[4].centres_mm:
                raise ValueError(tr("set_clamp_centres_or_their_count_in_the_clamps"))
        except Exception as exc:
            details.setPlainText(str(exc))
            return
        state["snapshot"] = snap
        cancel, queue = Event(), Queue()
        state["cancel"], state["queue"] = cancel, queue
        _, bends, span, catalog, clamps, _ = snap
        def worker():
            try:
                result = shortlist(catalog, bends, clamps, required_span_mm=span,
                                   cancelled=cancel.is_set)
                queue.put((result, None))
            except Exception as exc:
                queue.put((None, str(exc)))
        search.setEnabled(False)
        stop.setEnabled(True)
        details.setPlainText(tr("Searching"))
        timer.start()
        Thread(target=worker, daemon=True, name="BendSeqToolSelection").start()

    def cancel_search(*_):
        if state["cancel"] is not None:
            state["cancel"].set()

    def apply_pair(*_):
        candidate = selected()
        if candidate is None or workspace._task_progress_busy or workspace.timer.isActive() or workspace.playing:
            return
        try:
            previous = state["snapshot"]
            current = _snapshot(workspace)
            if current[0] is not previous[0] or current[1:] != previous[1:]:
                raise ValueError(tr("Part, sequence, library or clamps changed. Search again."))
            punch = workspace.tool_library.load_tool(candidate.punch_id, tool_kind.PUNCH)
            die = workspace.tool_library.load_tool(candidate.die_id, tool_kind.DIE)
            # The existing select methods invalidate old check results and redraw.
            workspace.select_punch(punch)
            workspace.select_die(die)
            if (getattr(workspace.tooling_setup.punch, "tool_id", None) != candidate.punch_id
                    or getattr(workspace.tooling_setup.die, "tool_id", None) != candidate.die_id):
                raise ValueError(tr("Tool selection failed; inspect the active tooling."))
            details.appendPlainText(tr("Pair selected. Use Calculate tool layout, FAST and Mesh to verify."))
        except Exception as exc:
            details.appendPlainText(str(exc))

    timer.timeout.connect(poll)
    search.clicked.connect(start)
    stop.clicked.connect(cancel_search)
    choices.currentIndexChanged.connect(refresh)
    apply.clicked.connect(apply_pair)
    dialog.finished.connect(cancel_search)
    dialog.destroyed.connect(cancel_search)
    embed(workspace, dialog, tr("Auto Tool Selection"), "auto_tool_selection")
