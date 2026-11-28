"""Native contextual shortlist with bounded FAST/2D review and local diagnostics."""
from dataclasses import asdict, replace
from math import dist
from queue import Queue, Empty
from threading import Event, Thread
from time import monotonic
from uuid import uuid4
import traceback

from freecad.bendseq.core.geometry_backend.io.tooling_library import ToolingLibrary
from freecad.bendseq.core.kinematics2d.auto_tool_selection import BendRequirement, shortlist
from freecad.bendseq.core.kinematics2d.auto_tool_fast_review import review_pair, PairFastReview
from freecad.bendseq.core.kinematics2d.developer_debug_log import debug_event
from freecad.bendseq.core.kinematics2d.shared_strokes import stroke_axis
from freecad.bendseq.core.kinematics2d.tooling_assembly import ToolAssembly
from freecad.bendseq.ui.localization import tr


def _snapshot(workspace):
    session = workspace.session
    bends = session.fold_state.bends
    requirements = tuple(BendRequirement(bid, dist(*bends[bid].axis_xyz),
                         session.local_bend_dimensions_mm.get(bid))
                         for bid in session.canonical_order)
    if session.controller.has_stages:
        # Separate pre-bend/finish operations share their physical bend length.
        span = max(b.length_mm for b in requirements)
    else:
        strokes = workspace._continuous_tool_strokes(workspace._tool_assembly_order())
        span = max(dist(*stroke_axis(group, bends)) for group in strokes)
    catalog = ToolingLibrary(workspace.tool_library.library_root).list_tools()
    return (session, requirements, span, catalog, workspace._current_clamp_config(),
            tuple(session.bend_order), tuple(session.controller.operation_steps),
            tuple(sorted(workspace._manual_operation_orientation_overrides.items())),
            tuple(sorted(workspace._manual_orientation_overrides.items())),
            tuple(sorted(workspace._fast_advisory_orientation_keys)),
            tuple(sorted(workspace._selected_operation_weights().to_dict().items())),
            repr(workspace.backgauge_model))


def show_auto_tool_selection(workspace, qt, embed, tool_kind):
    QtCore, _, widgets = qt()
    dialog = widgets.QDialog(workspace)
    layout = widgets.QVBoxLayout(dialog)
    note = widgets.QLabel(tr("auto_tool_note"))
    note.setWordWrap(True)
    layout.addWidget(note)
    search = widgets.QPushButton(tr("auto_tool_search"))
    stop = widgets.QPushButton(tr("auto_tool_stop"))
    stop.setEnabled(False)
    layout.addWidget(search)
    layout.addWidget(stop)
    choices = widgets.QComboBox()
    layout.addWidget(choices)
    details = widgets.QPlainTextEdit()
    details.setReadOnly(True)
    layout.addWidget(details)
    apply = widgets.QPushButton(tr("auto_tool_apply"))
    apply.setEnabled(False)
    layout.addWidget(apply)
    timer = QtCore.QTimer(dialog)
    timer.setInterval(100)
    state = {"snapshot": None, "result": None, "cancel": None, "queue": None,
             "run": None, "reviews": {}, "rows": [], "pending": [],
             "reviewing": False, "closed": False, "started": 0.0}

    def log(event, **data):
        debug_event("AUTO_TOOL_" + event, run_id=state["run"], **data)

    def error(event, exc):
        log(event, error=str(exc), exception_type=type(exc).__name__,
            traceback=traceback.format_exc())

    def selected():
        index = choices.currentIndex()
        return state["rows"][index] if 0 <= index < len(state["rows"]) else None

    def key(c):
        return c.punch_id, c.die_id

    def busy():
        return timer.isActive() or state["reviewing"]

    def valid_snapshot():
        previous = state["snapshot"]
        current = _snapshot(workspace)
        if (previous is None or current[0] is not previous[0] or current[1:] != previous[1:]
                or not workspace.session.can_reorder() or workspace._task_progress_busy
                or workspace.timer.isActive() or workspace.playing):
            raise ValueError(tr("auto_tool_stale"))
        return current

    def refresh(*_):
        candidate = selected()
        result = state["result"]
        review = state["reviews"].get(key(candidate)) if candidate else None
        apply.setEnabled(candidate is not None and not busy()
                         and review is not None and review.candidate is not None)
        if result is None:
            return
        lines = [tr("auto_tool_examined") + ": " + str(result.examined),
                 tr("auto_tool_complete") if result.complete else tr("auto_tool_limited")]
        if candidate is not None:
            lines += [candidate.status, *candidate.warnings]
            for kind in ("punch", "die"):
                segments = candidate.plan[kind + "_segments"]
                lines.append(tr(kind) + ": " + " + ".join(
                    f"{seg['length_mm']:g}" for seg in segments) + " mm")
            if review:
                lines.append("FAST / 2D: " + review.status)
                if review.candidate:
                    lines += [" -> ".join(review.candidate.operation_ids),
                              "Weight: " + str(round(review.candidate.estimated_cost, 3))]
        lines += ["", tr("auto_tool_rejected")]
        lines += [" / ".join(filter(None, (p, d))) + ": " + reason
                  for p, d, reason in result.rejected[:100]]
        details.setPlainText("\n".join(lines))

    def finish_reviews(reason):
        state["reviewing"] = False
        search.setEnabled(True)
        stop.setEnabled(False)
        rows = sorted(state["result"].candidates,
                      key=lambda c: (state["reviews"].get(key(c), PairFastReview("NOT_CHECKED")).rank(),
                                     c.score))
        state["rows"] = rows
        choices.clear()
        for c in rows:
            opening = "?" if c.opening_mm is None else f"{c.opening_mm:g}"
            review = state["reviews"].get(key(c))
            choices.addItem(f"{c.punch_id} / {c.die_id} | V {opening} mm | "
                            + (review.status if review else "NOT_CHECKED"))
        log("FINISHED", reason=reason, elapsed_s=monotonic()-state["started"],
            reviewed=len(state["reviews"]), total_candidates=len(rows),
            ranking=[dict(punch=c.punch_id, die=c.die_id,
                          status=state["reviews"][key(c)].status if key(c) in state["reviews"] else "NOT_CHECKED")
                     for c in rows])
        refresh()

    def review_next():
        if state["closed"]:
            return
        if state["cancel"].is_set() or monotonic()-state["started"] >= 40 or not state["pending"]:
            finish_reviews("CANCELLED" if state["cancel"].is_set() else
                           "LIMIT" if state["pending"] else "REVIEWED_SHORTLIST")
            return
        try:
            snap = valid_snapshot()
        except Exception as exc:
            error("STALE", exc)
            state["reviews"].clear()
            finish_reviews("STALE")
            return
        c = state["pending"].pop(0)
        pair = key(c)
        started = monotonic()
        log("REVIEW_STARTED", punch=pair[0], die=pair[1], plan=c.plan, warnings=c.warnings)
        def pair_log(event, **data):
            log(event, punch=pair[0], die=pair[1], **data)
        try:
            pair_log("PROFILE_LOAD_STARTED")
            punch = workspace.tool_library.load_tool(c.punch_id, tool_kind.PUNCH)
            die = workspace.tool_library.load_tool(c.die_id, tool_kind.DIE)
            setup = replace(workspace.tooling_setup, tool_assembly=None,
                            match_active_bend_length=False, punch_section_offsets_xm=(),
                            die_section_offsets_xm=())
            setup.select_punch(punch)
            setup.select_die(die)
            assembly = ToolAssembly.from_plan(c.plan)
            setup.set_tool_assembly(assembly)
            setup.align_to_cross_section(workspace.session.cross_section(segments=16),
                clearance_mm=workspace.cycle_config.initial_clearance_mm,
                bend_id=workspace.session.active_bend_id)
            pair_log("PROFILE_LOADED", punch_file=punch.source_file, die_file=die.source_file,
                     punch_reversed=getattr(punch, "punch_reversed", False),
                     punch_validation=str(punch.validation_status), die_validation=str(die.validation_status),
                     assembly=asdict(assembly))
            operations = snap[6]
            operation_pins, bend_pins, advisory = dict(snap[7]), dict(snap[8]), set(snap[9])
            pinned = {}
            for step in operations:
                if step.operation_id not in advisory:
                    if step.operation_id in operation_pins:
                        pinned[step.operation_id] = operation_pins[step.operation_id]
                    elif step.bend_id in bend_pins:
                        pinned[step.operation_id] = bend_pins[step.bend_id]
            review = review_pair(snap[0].fold_state, operations, snap[0].panel_vertices_xy,
                                 setup, pinned=pinned, weights=dict(snap[10]),
                                 backgauge_model=workspace.backgauge_model, trace=pair_log)
            state["reviews"][pair] = review
            pair_log("REVIEW_RESULT", status=review.status,
                     candidate=review.candidate.to_dict() if review.candidate else None,
                     elapsed_s=monotonic()-started)
        except Exception as exc:
            state["reviews"][pair] = PairFastReview("ERROR")
            pair_log("REVIEW_ERROR", error=str(exc), traceback=traceback.format_exc(),
                     elapsed_s=monotonic()-started)
        details.setPlainText(tr("auto_tool_reviewing") + ": " + str(len(state["reviews"])))
        QtCore.QTimer.singleShot(0, review_next)

    def poll():
        try:
            result, failure = state["queue"].get_nowait()
        except Empty:
            return
        timer.stop()
        if failure:
            search.setEnabled(True)
            stop.setEnabled(False)
            details.setPlainText(failure)
            return
        state["result"] = result
        state["pending"] = list(result.candidates[:5])
        state["reviewing"] = True
        log("SHORTLIST", candidates=[asdict(c) for c in result.candidates],
            rejected=result.rejected, complete=result.complete, examined=result.examined)
        QtCore.QTimer.singleShot(0, review_next)

    def start(*_):
        if busy():
            return
        state.update(result=None, reviews={}, rows=[], pending=[], run=uuid4().hex,
                     started=monotonic())
        choices.clear()
        apply.setEnabled(False)
        try:
            snap = _snapshot(workspace)
            if not snap[4].centres_mm:
                raise ValueError(tr("set_clamp_centres_or_their_count_in_the_clamps"))
            if not snap[0].can_reorder():
                raise ValueError(tr("auto_tool_stale"))
            log("STARTED", part=snap[0].part_label, bends=[asdict(b) for b in snap[1]],
                span_mm=snap[2], order=snap[5], operations=[asdict(o) for o in snap[6]],
                clamps=asdict(snap[4]), tool_count=len(snap[3].all_tools()),
                tools=[dict(id=t.tool_id, kind=t.kind, path=str(t.fcstd_path), metadata=t.metadata)
                       for t in snap[3].all_tools()], weights=dict(snap[10]),
                fast_pair_limit=5, total_budget_s=40)
        except Exception as exc:
            error("START_ERROR", exc)
            details.setPlainText(str(exc))
            return
        state["snapshot"] = snap
        cancel, queue = Event(), Queue()
        state["cancel"], state["queue"] = cancel, queue
        run_id = state["run"]
        _, bends, span, catalog, clamps = snap[:5]
        def worker():
            def trace(event, **data):
                debug_event("AUTO_TOOL_" + event, run_id=run_id, **data)
            try:
                result = shortlist(catalog, bends, clamps, required_span_mm=span,
                                   cancelled=cancel.is_set, trace=trace)
                queue.put((result, None))
            except Exception as exc:
                trace("INVENTORY_ERROR", error=str(exc), traceback=traceback.format_exc())
                queue.put((None, str(exc)))
        search.setEnabled(False)
        stop.setEnabled(True)
        details.setPlainText(tr("auto_tool_searching"))
        timer.start()
        Thread(target=worker, daemon=True, name="BendSeqToolSelection").start()

    def cancel_search(*_):
        if state["cancel"] is not None and busy():
            state["cancel"].set()
            log("CANCEL_REQUESTED")

    def close(*_):
        state["closed"] = True
        cancel_search()

    def apply_pair(*_):
        c = selected()
        if c is None or busy():
            return
        log("APPLY_STARTED", punch=c.punch_id, die=c.die_id)
        try:
            valid_snapshot()
            punch = workspace.tool_library.load_tool(c.punch_id, tool_kind.PUNCH)
            die = workspace.tool_library.load_tool(c.die_id, tool_kind.DIE)
            assembly = ToolAssembly.from_plan(c.plan)
            # Validate source CAD sections before touching the live tooling.
            from freecad.bendseq.core.kinematics2d.verified_tool_spans import verified_fixed_tool_spans
            setup = replace(workspace.tooling_setup, tool_assembly=None,
                            match_active_bend_length=False, punch_section_offsets_xm=(),
                            die_section_offsets_xm=())
            setup.select_punch(punch)
            setup.select_die(die)
            setup.set_tool_assembly(assembly)
            log("APPLY_SOURCE_CAD_VERIFY_STARTED", assembly=asdict(assembly))
            verified = verified_fixed_tool_spans(setup)
            log("APPLY_SOURCE_CAD_VERIFY_RESULT", report=verified.to_dict())
            workspace.select_punch(punch)
            workspace.select_die(die)
            if (getattr(workspace.tooling_setup.punch, "tool_id", None) != c.punch_id
                    or getattr(workspace.tooling_setup.die, "tool_id", None) != c.die_id):
                raise ValueError(tr("auto_tool_apply_error"))
            log("APPLY_LAYOUT_STARTED", assembly=asdict(assembly))
            workspace._apply_tool_assembly(assembly)
            log("APPLY_RESULT", punch=c.punch_id, die=c.die_id, assembly=asdict(assembly),
                evidence="PAIR_AND_LAYOUT_APPLIED_SEQUENCE_NOT_APPLIED")
            details.appendPlainText(tr("auto_tool_applied"))
        except Exception as exc:
            error("APPLY_ERROR", exc)
            details.appendPlainText(str(exc))

    timer.timeout.connect(poll)
    search.clicked.connect(start)
    stop.clicked.connect(cancel_search)
    choices.currentIndexChanged.connect(refresh)
    apply.clicked.connect(apply_pair)
    dialog.finished.connect(close)
    dialog.destroyed.connect(close)
    embed(workspace, dialog, tr("auto_tool_title"), "auto_tool_selection")
