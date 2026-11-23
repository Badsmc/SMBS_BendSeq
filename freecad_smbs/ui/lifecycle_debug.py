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

    # Keep a strong reference to the native FreeCAD main window for
    # shutdown attribution. Qt dialogs can close independently; neither
    # their DeferredDelete nor their acceptance proves application exit.
    try:
        import FreeCADGui as Gui
        freecad_main_window = Gui.getMainWindow()
    except (ImportError, AttributeError, RuntimeError):
        freecad_main_window = None

    class Filter(QtCore.QObject):
        def eventFilter(self, obj, event):
            try:
                kind = event.type()
                if kind == QtCore.QEvent.Close and obj is freecad_main_window:
                    # This is the main-window CLOSE REQUEST, before FreeCAD
                    # destroys its documents. Do not ignore it or alter Qt's
                    # shutdown behavior; this is diagnostics only.
                    record(
                        'FREECAD_MAIN_WINDOW_CLOSE_REQUESTED',
                        documents=list(App.listDocuments()),
                        active_window=str(getattr(
                            QtWidgets.QApplication.activeWindow(),
                            'objectName',lambda:'' )()),
                    )
                if kind == QtCore.QEvent.Quit and obj is QtWidgets.QApplication.instance():
                    record('QT_APPLICATION_QUIT', documents=list(App.listDocuments()))
                if kind in (QtCore.QEvent.Close, QtCore.QEvent.DeferredDelete,
                            QtCore.QEvent.ParentChange):
                    # QApplication event filters can see QEvent receivers
                    # without a QObject API (e.g. PySide6 QWidgetItem).
                    # Never let optional lifetime diagnostics raise during
                    # widget teardown or FreeCAD application shutdown.
                    if not isinstance(obj, QtCore.QObject):
                        return False
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
    # Record actual Qt application shutdown even when QEvent.Quit is
    # delivered after FreeCAD has already closed all model documents.
    # Keep callback references in _installed below.
    def about_to_quit():
        try:
            record(
                'QT_ABOUT_TO_QUIT',
                documents=list(App.listDocuments()),
                freecad_main_visible=(
                    bool(freecad_main_window.isVisible())
                    if freecad_main_window is not None else None
                ),
            )
        except Exception:
            debug_event(
                'LIFETIME_DIAGNOSTIC_ERROR',
                traceback=traceback.format_exc(),
            )
    QtWidgets.QApplication.instance().aboutToQuit.connect(about_to_quit)
    QtWidgets.QApplication.instance().installEventFilter(event_filter)
    sys.excepthook = exception_hook
    _installed = (observer, event_filter, exception_hook, about_to_quit)
    debug_event('LIFETIME_DIAGNOSTICS_INSTALLED', documents=list(App.listDocuments()))
