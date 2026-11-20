"""Session diagnostics for document and Qt lifetime failures."""
from __future__ import annotations
import sys
import traceback
from datetime import datetime, timezone

_installed = None
_native_fault_file = None


def _enable_native_fault_diagnostics():
    """Capture Python thread stacks on native faults on supported platforms.

    A QTreeWidget/PySide/FreeCAD native fault cannot be caught by ordinary
    try/except. Keep the stream alive for the entire FreeCAD process.
    Do not replace a faulthandler that FreeCAD already registered.
    """
    global _native_fault_file
    from smbs_core.kinematics2d.developer_debug_log import (
        debug_event, desktop_directory,
    )
    try:
        import faulthandler

        if _native_fault_file is not None:
            return
        if faulthandler.is_enabled():
            debug_event(
                "NATIVE_FAULT_DIAGNOSTICS",
                status="ALREADY_ENABLED_BY_HOST",
            )
            return
        path = desktop_directory() / "SMBS_native_crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a", encoding="utf-8", buffering=1)
        try:
            stream.write(
                "\nSMBS FreeCAD native fault monitor enabled: "
                + datetime.now(timezone.utc).isoformat()
                + "\n"
            )
            stream.flush()
            faulthandler.enable(file=stream, all_threads=True)
        except Exception:
            stream.close()
            raise
        _native_fault_file = stream
        debug_event("NATIVE_FAULT_DIAGNOSTICS", status="ENABLED", path=str(path))
    except (OSError, RuntimeError, ValueError) as exc:
        debug_event("NATIVE_FAULT_DIAGNOSTICS", status="UNAVAILABLE", reason=str(exc))


def install(QtCore, QtWidgets):
    global _installed
    if _installed is not None:
        return
    import FreeCAD as App
    from smbs_core.kinematics2d.developer_debug_log import debug_event
    _enable_native_fault_diagnostics()

    def record(event, **data):
        debug_event(event, stack=traceback.format_stack(limit=24), **data)

    def document_event(kind, doc):
        record(kind, document=str(getattr(doc, 'Name', '')),
               filename=str(getattr(doc, 'FileName', '')),
               documents=list(App.listDocuments()))

    class Observer:
        def slotCreatedDocument(self, doc):
            document_event('DOCUMENT_CREATED', doc)
        def slotDeletedDocument(self, doc):
            document_event('DOCUMENT_DELETED', doc)
        def slotBeforeCloseDocument(self, doc):
            document_event('DOCUMENT_BEFORE_CLOSE', doc)
        def slotActivateDocument(self, doc):
            document_event('DOCUMENT_ACTIVATED', doc)
        def slotStartSaveDocument(self, doc, *args):
            document_event('DOCUMENT_SAVE_STARTED', doc)
        def slotFinishSaveDocument(self, doc, *args):
            document_event('DOCUMENT_SAVE_FINISHED', doc)

    class Filter(QtCore.QObject):
        def eventFilter(self, obj, event):
            try:
                kind = event.type()
                if kind == QtCore.QEvent.Quit and obj is QtWidgets.QApplication.instance():
                    record('QT_APPLICATION_QUIT', documents=list(App.listDocuments()))
                if kind in (QtCore.QEvent.Close, QtCore.QEvent.DeferredDelete,
                            QtCore.QEvent.ParentChange):
                    name = obj.objectName()
                    if ('SMBS' in name or isinstance(obj, QtWidgets.QMdiSubWindow)):
                        record('QT_LIFETIME_EVENT', event_type=str(kind),
                               object_name=name, qt_class=obj.metaObject().className(),
                               title=obj.windowTitle() if isinstance(obj, QtWidgets.QWidget) else '')
            except Exception:
                debug_event('LIFETIME_DIAGNOSTIC_ERROR', traceback=traceback.format_exc())
            return False

    previous = sys.excepthook
    def exception_hook(kind, value, tb):
        debug_event('UNHANDLED_PYTHON_EXCEPTION',
                    traceback=''.join(traceback.format_exception(kind, value, tb)))
        previous(kind, value, tb)

    observer = Observer()
    event_filter = Filter(QtWidgets.QApplication.instance())
    App.addDocumentObserver(observer)
    QtWidgets.QApplication.instance().installEventFilter(event_filter)
    sys.excepthook = exception_hook
    _installed = (observer, event_filter, exception_hook)
    debug_event('LIFETIME_DIAGNOSTICS_INSTALLED', documents=list(App.listDocuments()))
