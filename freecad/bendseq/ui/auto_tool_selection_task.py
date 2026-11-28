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
from freecad.bendseq.core.kinematics2d.auto_tool_apply import pinned_orientations, verify_candidate_layout
from freecad.bendseq.core.kinematics2d.auto_tool_retry import (attempt_order, mesh_outcome, next_action, TOTAL_MESH_BUDGET_S)
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
    # File changes may not alter the parsed sidecar metadata. Record source
    # and sidecar stat identity to invalidate stale candidates before reuse.
    def stamp(path):
        if path is None:
            return None
        info = path.stat()
        return (str(path), info.st_size, info.st_mtime_ns)
    files = tuple((stamp(tool.fcstd_path), stamp(tool.sidecar_path))
                  for tool in catalog.all_tools())
    from freecad.bendseq.ui.mesh_preferences import mesh_defaults
    return (session, requirements, span, catalog, workspace._current_clamp_config(),
            tuple(session.bend_order), tuple(session.controller.operation_steps),
            tuple(sorted(workspace._manual_operation_orientation_overrides.items())),
            tuple(sorted(workspace._manual_orientation_overrides.items())),
            tuple(sorted(workspace._fast_advisory_orientation_keys)),
            tuple(sorted(workspace._selected_operation_weights().to_dict().items())),
            repr(workspace.backgauge_model), files, repr(workspace.cycle_config),
            bool(workspace.tooling_setup.allow_end_zone_collision),
            float(workspace.tooling_setup.end_zone_length_mm), tuple(mesh_defaults()))


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
    apply = widgets.QPushButton(tr("auto_tool_apply") + " + " + tr("apply_unverified_sequence"))
    apply.setEnabled(False)
    layout.addWidget(apply)
    run_mesh = widgets.QCheckBox(tr("run_mesh"))
    run_mesh.setChecked(True)
    layout.addWidget(run_mesh)
    retry_mesh = widgets.QCheckBox(tr("auto_tool_retry_collision"))
    retry_mesh.setChecked(False)
    run_mesh.toggled.connect(retry_mesh.setEnabled)
    layout.addWidget(retry_mesh)
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
            pinned = pinned_orientations(operations, dict(snap[7]), dict(snap[8]), set(snap[9]))
            review = review_pair(snap[0].fold_state, operations, snap[0].panel_vertices_xy,
                                 setup, pinned=pinned, weights=dict(snap[10]),
                                 backgauge_model=workspace.backgauge_model, trace=pair_log)
            if review.candidate is not None:
                try:
                    strokes = verify_candidate_layout(review.candidate, operations,
                                                     snap[0].fold_state.bends, assembly, pinned)
                    pair_log("FAST_LAYOUT_COVERAGE", strokes=strokes)
                except ValueError as exc:
                    pair_log("FAST_LAYOUT_REJECTED", error=str(exc), candidate=review.candidate.to_dict())
                    review = PairFastReview("ERROR", fast_report=review.fast_report,
                                            screen_report=review.screen_report)
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
        review = state["reviews"].get(key(c))
        if review is None or review.candidate is None:
            return
        try:
            snap = valid_snapshot()
        except Exception as exc:
            error("APPLY_ERROR", exc)
            details.appendPlainText(str(exc))
            return
        candidate = review.candidate
        check_mesh = run_mesh.isChecked()
        run_id = state["run"]
        retry = check_mesh and retry_mesh.isChecked()
        ranked = [(row, state["reviews"][key(row)].candidate)
                  for row in state["rows"] if key(row) in state["reviews"]
                  and state["reviews"][key(row)].candidate is not None]
        attempts = attempt_order((c, candidate), ranked, retry=retry)
        log("APPLY_REQUESTED", punch=c.punch_id, die=c.die_id,
            candidate=candidate.to_dict(), mesh_requested=check_mesh,
            retry_enabled=retry, attempts=[(x.punch_id, x.die_id) for x, _ in attempts])
        dialog.accept()
        # Follow native FAST lifecycle: never mutate the CAD scene from a
        # contextual dialog's completion callback.
        QtCore.QTimer.singleShot(0, lambda: _start_attempts(
            workspace, tool_kind, snap, attempts, run_id, check_mesh, retry, QtCore))

    timer.timeout.connect(poll)
    search.clicked.connect(start)
    stop.clicked.connect(cancel_search)
    choices.currentIndexChanged.connect(refresh)
    apply.clicked.connect(apply_pair)
    dialog.finished.connect(close)
    dialog.destroyed.connect(close)
    embed(workspace, dialog, tr("auto_tool_title"), "auto_tool_selection")


def _apply_selection(workspace, tool_kind, snap, c, candidate, run_id, check_mesh):
    def log(event, **data):
        debug_event("AUTO_TOOL_" + event, run_id=run_id, **data)

    old_setup = None
    changed = False
    sequence_applied = False
    try:
        if not workspace._playback_alive():
            log("APPLY_SKIPPED", reason="workspace_destroyed")
            return
        current = _snapshot(workspace)
        if (current[0] is not snap[0] or current[1:] != snap[1:]
                or not workspace.session.can_reorder() or workspace._task_progress_busy
                or workspace.timer.isActive() or workspace.playing):
            raise ValueError(tr("auto_tool_stale"))
        operations = snap[6]
        pinned = pinned_orientations(operations, dict(snap[7]), dict(snap[8]), set(snap[9]))
        assembly = ToolAssembly.from_plan(c.plan)
        strokes = verify_candidate_layout(candidate, operations, snap[0].fold_state.bends,
                                         assembly, pinned)
        log("APPLY_STARTED", punch=c.punch_id, die=c.die_id, candidate=candidate.to_dict(),
            strokes=strokes, mesh_requested=check_mesh)
        punch = workspace.tool_library.load_tool(c.punch_id, tool_kind.PUNCH)
        die = workspace.tool_library.load_tool(c.die_id, tool_kind.DIE)
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
        old_setup = replace(workspace.tooling_setup)
        changed = True
        workspace.select_punch(punch)
        workspace.select_die(die)
        if (getattr(workspace.tooling_setup.punch, "tool_id", None) != c.punch_id
                or getattr(workspace.tooling_setup.die, "tool_id", None) != c.die_id):
            raise ValueError(tr("auto_tool_apply_error"))
        log("APPLY_LAYOUT_STARTED", assembly=asdict(assembly))
        workspace._apply_tool_assembly(assembly)
        if workspace.tooling_setup.tool_assembly != assembly:
            raise RuntimeError("Selected assembly was not applied")
        log("APPLY_SEQUENCE_STARTED", candidate=candidate.to_dict(), pinned=pinned)
        # Use the same code path as the normal FAST result dialog.
        workspace._set_task_result("FAST", "running", tr("apply_unverified_sequence"))
        if workspace._apply_fast_sequence_candidate(candidate, snap[0].controller,
                                                     operations, pinned, 0) is not True:
            raise RuntimeError("Existing FAST sequence application did not complete")
        sequence_applied = True
        actual = tuple(op.operation_id for op in workspace.session.controller.operation_steps)
        actual_orientations = workspace._effective_operation_orientation_map()
        if (actual != tuple(candidate.operation_ids)
                or any(actual_orientations.get(k) != v for k, v in candidate.orientations().items())
                or workspace.tooling_setup.tool_assembly != assembly):
            raise RuntimeError("Applied order, orientation or layout differs from FAST candidate")
        log("APPLY_RESULT", punch=c.punch_id, die=c.die_id, order=actual,
            orientations=actual_orientations, assembly=asdict(assembly),
            evidence="APPLIED_UNVERIFIED_3D", mesh_requested=check_mesh)
        return workspace._mesh_preview_identity(), _snapshot(workspace)
    except Exception as exc:
        log("APPLY_ERROR", error=str(exc), traceback=traceback.format_exc(),
            changed_tooling=changed, sequence_applied=sequence_applied)
        if changed and not sequence_applied and old_setup is not None and workspace._playback_alive():
            try:
                # Preserve object identity; the press-cycle controller may refer to it.
                workspace.tooling_setup.__dict__.update(old_setup.__dict__)
                blocked = workspace.virtual_tool_len_checkbox.blockSignals(True)
                try:
                    workspace.virtual_tool_len_checkbox.setChecked(old_setup.match_active_bend_length)
                finally:
                    workspace.virtual_tool_len_checkbox.blockSignals(blocked)
                workspace.press_cycle = None
                workspace._refresh_tooling_combos()
                workspace._update_tooling_status_label()
                workspace.draw()
                log("APPLY_TOOLING_ROLLBACK")
            except Exception as rollback:
                log("APPLY_ROLLBACK_ERROR", error=str(rollback), traceback=traceback.format_exc())
        if workspace._playback_alive():
            workspace.status.setText("Auto Tool Selection: " + str(exc))


def _start_attempts(workspace, tool_kind, snap, attempts, run_id, check_mesh, retry, QtCore):
    """Only the user-approved bounded shortlist; no hidden changes after errors."""
    started = monotonic()
    history = []
    initial_pins = pinned_orientations(snap[6], dict(snap[7]), dict(snap[8]), set(snap[9]))

    def log(event, **data):
        debug_event("AUTO_TOOL_" + event, run_id=run_id, **data)

    def finish(reason):
        log("RETRY_FINISHED", reason=reason, attempts=history,
            elapsed_s=monotonic()-started,
            evidence="SAMPLED_ONLY_NOT_CONTINUOUS_CERTIFICATION")
        if workspace._playback_alive() and check_mesh:
            workspace.status.setText(tr("auto_tool_retry_summary",
                outcome=reason, count=len(history)))
        # The last applied configuration remains visible with its own Mesh
        # evidence; never silently restore or label another candidate clear.

    def attempt(index, expected_snapshot, expected_identity=None):
        if not workspace._playback_alive():
            finish("WORKSPACE_DESTROYED")
            return
        try:
            if (workspace.session is None or workspace._task_progress_busy
                    or workspace.timer.isActive() or workspace.playing):
                finish("SETUP_CHANGED_OR_BUSY")
                return
            current = _snapshot(workspace)
            if (current[0] is not expected_snapshot[0] or current[1:] != expected_snapshot[1:]
                    or (expected_identity is not None and workspace._mesh_preview_identity() != expected_identity)):
                finish("SETUP_CHANGED_OR_BUSY")
                return
            if pinned_orientations(current[6], dict(current[7]), dict(current[8]), set(current[9])) != initial_pins:
                finish("OPERATOR_PINS_CHANGED")
                return
            remaining = TOTAL_MESH_BUDGET_S - (monotonic() - started)
            if retry and remaining <= 0:
                finish("TOTAL_BUDGET_EXCEEDED")
                return
            tool, candidate = attempts[index]
            record = dict(attempt=index+1, punch=tool.punch_id, die=tool.die_id,
                          order=list(candidate.operation_ids))
            history.append(record)
            log("RETRY_ATTEMPT_STARTED", **record, remaining_budget_s=remaining if retry else None)
            applied = _apply_selection(workspace, tool_kind, current, tool, candidate, run_id, check_mesh)
            if applied is None:
                record["outcome"] = "APPLY_ERROR"
                finish("APPLY_ERROR")
                return
            identity, post_snapshot = applied
            if not check_mesh:
                record["outcome"] = "APPLIED_UNVERIFIED_3D"
                finish(record["outcome"])
                return
            def check():
                try:
                    if (not workspace._playback_alive() or workspace.session is None
                            or workspace._task_progress_busy or workspace.timer.isActive() or workspace.playing
                            or workspace._mesh_preview_identity() != identity):
                        log("MESH_SKIPPED", attempt=index+1, reason="setup_changed_or_busy")
                        finish("SETUP_CHANGED_OR_BUSY")
                        return
                    now = _snapshot(workspace)
                    if now[0] is not post_snapshot[0] or now[1:] != post_snapshot[1:]:
                        finish("SETUP_CHANGED_OR_BUSY")
                        return
                    remaining = TOTAL_MESH_BUDGET_S - (monotonic() - started)
                    if retry and remaining <= 0:
                        finish("TOTAL_BUDGET_EXCEEDED")
                        return
                    report = workspace._run_mesh_sequence_check(
                        auto_tool_run_id=run_id, auto_tool_attempt=index+1,
                        budget_cap_seconds=remaining if retry else None)
                    if not workspace._playback_alive():
                        finish("WORKSPACE_DESTROYED")
                        return
                    if workspace.session is None or workspace._mesh_preview_identity() != identity:
                        finish("SETUP_CHANGED_OR_BUSY")
                        return
                    outcome = mesh_outcome(report, candidate.operation_ids)
                    record.update(outcome=outcome,
                                  findings=len(report.get("findings", ())) if isinstance(report, dict) else None,
                                  elapsed_s=report.get("elapsed_seconds") if isinstance(report, dict) else None)
                    log("MESH_FINISHED", **record, returned_report=report is not None)
                    action = next_action(outcome, len(attempts)-index-1,
                                         TOTAL_MESH_BUDGET_S-(monotonic()-started)) if retry else outcome
                    log("RETRY_DECISION", attempt=index+1, outcome=outcome, action=action)
                    if action == "RETRY":
                        # Reuse the exact post-apply input snapshot. Any change
                        # while Mesh was running cancels the alternative.
                        QtCore.QTimer.singleShot(0, lambda: attempt(index+1, post_snapshot, identity))
                    else:
                        finish(action)
                except Exception as exc:
                    log("MESH_ERROR", attempt=index+1, error=str(exc), traceback=traceback.format_exc())
                    finish("ERROR")
            QtCore.QTimer.singleShot(0, check)
        except Exception as exc:
            log("RETRY_ERROR", attempt=index+1, error=str(exc), traceback=traceback.format_exc())
            finish("ERROR")

    attempt(0, snap)
