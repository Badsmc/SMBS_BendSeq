"""Session diagnostics for document and Qt lifetime failures."""
from __future__ import annotations
import sys
import traceback

_installed = None


def install(QtCore, QtWidgets):
    global _installed
    if _installed is not None:
        return
    import FreeCAD as App
    from smbs_core.kinematics2d.developer_debug_log import debug_event

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
